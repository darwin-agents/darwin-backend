"""
Finance service tests — ledger integrity, concurrency, and boundary conditions.

Critical invariants (spec §19.3):
  1. A debit that results in negative balance is always rejected.
  2. Row-level locking prevents double-spend under concurrent requests.
  3. Raw PAN must never appear in response bodies or exception messages.
  4. The low-balance alert fires at ≤ 20% of initial budget.
  5. Every transaction is immutably recorded (INSERT, never UPDATE).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.finance import LedgerUpdateRequest, VCCPayload, VaultSubmitRequest
from services.ledger_service import InsufficientFundsError, LedgerService
from tests.conftest import agent_headers, human_headers, make_pool


# ── Helpers ────────────────────────────────────────────────────────────────────

def _req(amount: str, tx_type: str = "VCC_DEBIT", darwin_id: str = "agent-1") -> LedgerUpdateRequest:
    return LedgerUpdateRequest(
        darwin_id=darwin_id,
        session_id="sess-1",
        amount=Decimal(amount),
        type=tx_type,
        description="test transaction",
    )


def _settings(low_pct: float = 0.20) -> MagicMock:
    s = MagicMock()
    s.low_balance_pct = low_pct
    return s


def _pool(balance: float, initial: float = 50.0):
    pool, conn = make_pool(
        fetchrow_returns=[
            {"ledger_balance": balance, "initial_budget": initial},
            {"id": "tx-uuid-1"},
        ]
    )
    return pool, conn


# ── Credit tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_increases_balance():
    pool, _ = _pool(balance=30.0)
    result = await LedgerService(pool, _settings()).apply_transaction(
        _req("+15.00", "REVENUE_CREDIT")
    )
    assert result.new_balance == Decimal("45.00")


@pytest.mark.asyncio
async def test_credit_returns_correct_transaction_id():
    pool, _ = _pool(balance=10.0)
    result = await LedgerService(pool, _settings()).apply_transaction(
        _req("+5.00", "REVENUE_CREDIT")
    )
    assert result.transaction_id == "tx-uuid-1"


@pytest.mark.asyncio
async def test_credit_from_zero_balance():
    pool, _ = _pool(balance=0.0)
    result = await LedgerService(pool, _settings()).apply_transaction(
        _req("+100.00", "METABOLIC_INJECTION")
    )
    assert result.new_balance == Decimal("100.00")


# ── Debit tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_debit_within_balance_succeeds():
    pool, _ = _pool(balance=42.50)
    result = await LedgerService(pool, _settings()).apply_transaction(_req("-12.99"))
    assert result.new_balance == Decimal("29.51")


@pytest.mark.asyncio
async def test_debit_exact_balance_succeeds():
    """Edge case: debit exactly equal to balance must succeed (not result in negative)."""
    pool, _ = _pool(balance=50.00)
    result = await LedgerService(pool, _settings()).apply_transaction(_req("-50.00"))
    assert result.new_balance == Decimal("0.00")


@pytest.mark.asyncio
async def test_debit_one_cent_over_balance_rejected():
    """One cent over must be rejected — no tolerance for overdraft."""
    pool, _ = _pool(balance=10.00)
    with pytest.raises(InsufficientFundsError) as exc_info:
        await LedgerService(pool, _settings()).apply_transaction(_req("-10.01"))
    assert exc_info.value.balance == Decimal("10.00")
    assert exc_info.value.requested == Decimal("10.01")


@pytest.mark.asyncio
async def test_debit_rejected_insufficient_funds():
    pool, _ = _pool(balance=5.00)
    with pytest.raises(InsufficientFundsError):
        await LedgerService(pool, _settings()).apply_transaction(_req("-10.00"))


@pytest.mark.asyncio
async def test_debit_from_zero_balance_rejected():
    """Critical: zero balance must reject any debit."""
    pool, _ = _pool(balance=0.00)
    with pytest.raises(InsufficientFundsError):
        await LedgerService(pool, _settings()).apply_transaction(_req("-0.01"))


@pytest.mark.asyncio
async def test_insufficient_funds_error_carries_correct_values():
    pool, _ = _pool(balance=3.00)
    with pytest.raises(InsufficientFundsError) as exc_info:
        await LedgerService(pool, _settings()).apply_transaction(_req("-7.50"))
    err = exc_info.value
    assert err.balance == Decimal("3.00")
    assert err.requested == Decimal("7.50")
    # Error message must never contain a PAN or sensitive financial secret
    assert "pan" not in str(err).lower()
    assert "cvv" not in str(err).lower()


# ── Concurrency / race condition simulation ────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_debits_only_one_succeeds():
    """
    Simulate two concurrent debits of $30 against a $40 balance.
    Only one must succeed; the other must raise InsufficientFundsError.

    In production this is enforced by SELECT … FOR UPDATE row locking.
    Here we simulate serialized access by sharing a single call counter.
    """
    balance = Decimal("40.00")
    call_count = 0

    class SerializedConn:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def transaction(self): return self
        async def fetchrow(self, q, *a):
            nonlocal balance, call_count
            call_count += 1
            if "SELECT ledger_balance" in q:
                return {"ledger_balance": float(balance), "initial_budget": 40.0}
            if "INSERT INTO ledger_transactions" in q:
                balance -= Decimal("30.00")
                return {"id": "tx-race"}
            return None
        async def execute(self, *a): pass
        async def fetch(self, *a): return []

    class SerializedPool:
        def acquire(self): return SerializedConn()

    ledger = LedgerService(SerializedPool(), _settings())  # type: ignore[arg-type]
    req = _req("-30.00")

    results = await asyncio.gather(
        ledger.apply_transaction(req),
        ledger.apply_transaction(req),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, InsufficientFundsError)]

    assert len(successes) == 1
    assert len(failures) == 1


# ── DB write shape ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transaction_write_includes_correct_columns():
    pool, conn = _pool(balance=50.0)
    await LedgerService(pool, _settings()).apply_transaction(
        LedgerUpdateRequest(
            darwin_id="agent-1",
            session_id="sess-x",
            amount=Decimal("-8.00"),
            type="VCC_DEBIT",
            description="Browserbase checkout",
            external_ref="bb_session_abc",
        )
    )
    # The UPDATE and INSERT must both have been called
    assert len(conn.execute_calls) >= 1
    update_sql, update_args = conn.execute_calls[0]
    assert "UPDATE darwins" in update_sql
    assert Decimal("42.00") in update_args or float(Decimal("42.00")) in update_args


# ── Route-level finance tests ──────────────────────────────────────────────────

def test_ledger_update_rejects_human_jwt(client):
    """Human JWT must not be accepted on the agent-only ledger endpoint."""
    res = client.post(
        "/api/v1/finance/ledger/update",
        headers=human_headers(),
        json={"darwin_id": "agent-1", "session_id": "s", "amount": "-5.0",
              "type": "VCC_DEBIT", "description": "test"},
    )
    assert res.status_code in (401, 403)


def test_ledger_update_rejects_no_auth(client):
    res = client.post(
        "/api/v1/finance/ledger/update",
        json={"darwin_id": "agent-1", "session_id": "s", "amount": "-5.0",
              "type": "VCC_DEBIT", "description": "test"},
    )
    assert res.status_code in (401, 403, 422)


def test_inject_payment_agent_id_mismatch_rejected(client):
    """Agent JWT for agent-A must not inject payment on behalf of agent-B."""
    headers = agent_headers(darwin_id="agent-A")
    res = client.post(
        "/api/v1/finance/inject-payment",
        headers=headers,
        json={
            "darwin_id": "agent-B",  # different from JWT claim
            "vcc_ref": "vcc_ref",
            "browserbase_session_id": "bb_sess",
            "target_fields": {"pan_element": "#pan", "cvv_element": "#cvv", "expiry_element": "#exp"},
        },
    )
    assert res.status_code == 403


def test_finance_ledger_get_requires_human_jwt(client):
    res = client.get("/api/v1/finance/ledger/agent-1")
    assert res.status_code in (401, 403, 422)
