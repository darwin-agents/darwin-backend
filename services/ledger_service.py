"""
LedgerService — the only path through which balance mutations can flow.

All balance writes use SELECT … FOR UPDATE row-level locking so two concurrent
purchase requests from the same agent cannot both succeed if the combined cost
would exceed the balance.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import asyncpg

from config import Settings
from schemas.finance import LedgerUpdateRequest, LedgerUpdateResult


class InsufficientFundsError(Exception):
    def __init__(self, balance: Decimal, requested: Decimal) -> None:
        self.balance = balance
        self.requested = requested
        super().__init__(f"Insufficient funds: balance={balance}, requested={requested}")


class LedgerService:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:  # type: ignore[type-arg]
        self._pool = pool
        self._settings = settings

    async def apply_transaction(self, req: LedgerUpdateRequest) -> LedgerUpdateResult:
        """
        Apply a debit or credit with a row-level lock.
        Raises InsufficientFundsError if debit would make balance negative.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT ledger_balance, initial_budget FROM darwins WHERE id = $1 FOR UPDATE",
                    req.darwin_id,
                )
                if row is None:
                    raise ValueError(f"Darwin {req.darwin_id} not found")

                current = Decimal(str(row["ledger_balance"]))
                initial = Decimal(str(row["initial_budget"]))
                new_balance = current + req.amount

                if new_balance < 0:
                    raise InsufficientFundsError(balance=current, requested=abs(req.amount))

                await conn.execute(
                    "UPDATE darwins SET ledger_balance = $1, last_active_at = now() WHERE id = $2",
                    new_balance,
                    req.darwin_id,
                )
                tx = await conn.fetchrow(
                    """
                    INSERT INTO ledger_transactions
                      (darwin_id, amount, balance_after, type, description, external_ref)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    req.darwin_id,
                    req.amount,
                    new_balance,
                    req.type,
                    req.description,
                    req.external_ref,
                )

        # Fire-and-forget side effects (outside the transaction)
        asyncio.create_task(self._post_transaction(req, new_balance, initial))

        return LedgerUpdateResult(
            transaction_id=str(tx["id"]),
            new_balance=new_balance,
        )

    async def _post_transaction(
        self, req: LedgerUpdateRequest, new_balance: Decimal, initial: Decimal
    ) -> None:
        """Push canvas event and low-balance alert after the transaction commits."""
        from services.canvas_service import CanvasService
        from services.telegram_service import TelegramService

        canvas = CanvasService(self._pool)
        event_type = "LEDGER_CREDIT" if req.amount > 0 else "LEDGER_DEBIT"
        await canvas.push_event(
            darwin_id=req.darwin_id,
            session_id=req.session_id,
            event_type=event_type,
            payload={
                "amount": float(req.amount),
                "balance_after": float(new_balance),
                "type": req.type,
                "description": req.description,
                "external_ref": req.external_ref,
            },
        )

        # Low-balance threshold alert
        low = self._settings.low_balance_pct
        if req.amount < 0 and initial > 0 and new_balance <= initial * Decimal(str(low)):
            telegram = TelegramService(self._settings)
            pct = int((new_balance / initial) * 100)
            await telegram.send_alert_by_darwin_id(
                self._pool,
                req.darwin_id,
                f"⚠️ Agent is at {pct}% capital (${new_balance:.2f} remaining).",
            )

    async def get_ledger(self, darwin_id: str, limit: int = 50, offset: int = 0) -> dict:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ledger_balance, initial_budget FROM darwins WHERE id = $1", darwin_id
            )
            txs = await conn.fetch(
                """
                SELECT id, amount, balance_after, type, description, external_ref, created_at
                FROM ledger_transactions
                WHERE darwin_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                darwin_id, limit, offset,
            )
            totals = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS revenue,
                  COALESCE(SUM(ABS(amount)) FILTER (WHERE amount < 0), 0) AS burned
                FROM ledger_transactions WHERE darwin_id = $1
                """,
                darwin_id,
            )

        return {
            "darwin_id": darwin_id,
            "current_balance": float(row["ledger_balance"]) if row else 0,
            "initial_budget": float(row["initial_budget"]) if row else 0.0,
            "transactions": [dict(t) for t in txs],
            "total_revenue": float(totals["revenue"]),
            "total_burned": float(totals["burned"]),
        }
