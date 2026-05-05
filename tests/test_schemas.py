"""
Pydantic schema validation edge cases — every field validator tested.

These run without any I/O; they're pure model instantiation tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.agents import SpawnRequest
from schemas.canvas import NeuronSpawnedPayload, ThoughtPayload
from schemas.finance import (
    LedgerUpdateRequest,
    MicroTxRequest,
    VaultSubmitRequest,
    VCCPayload,
)
from schemas.memory import SkillUploadRequest
from schemas.telemetry import CanvasEventRequest, EntityPushRequest, VALID_EVENT_TYPES


# ── SpawnRequest ──────────────────────────────────────────────────────────────

class TestSpawnRequest:
    def test_valid_spawn(self):
        r = SpawnRequest(name="Scout", directive="find suppliers", initial_budget=50.0, vcc_ref="x")
        assert r.name == "Scout"
        assert r.requires_approval is True  # default

    def test_name_empty_rejected(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="", directive="find suppliers", initial_budget=50.0, vcc_ref="x")

    def test_directive_too_short_rejected(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="Scout", directive="short", initial_budget=50.0, vcc_ref="x")

    def test_budget_zero_rejected(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="Scout", directive="valid directive here", initial_budget=0, vcc_ref="x")

    def test_budget_negative_rejected(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="Scout", directive="valid directive here", initial_budget=-1.0, vcc_ref="x")

    def test_budget_over_limit_rejected(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="Scout", directive="valid directive here", initial_budget=10_001.0, vcc_ref="x")

    def test_budget_exactly_at_limit_accepted(self):
        r = SpawnRequest(name="Scout", directive="valid directive here", initial_budget=10_000.0, vcc_ref="x")
        assert r.initial_budget == 10_000.0

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            SpawnRequest(name="x" * 101, directive="valid directive here", initial_budget=50.0, vcc_ref="x")

    def test_requires_approval_defaults_true(self):
        r = SpawnRequest(name="Scout", directive="valid directive here", initial_budget=50.0, vcc_ref="x")
        assert r.requires_approval is True


# ── LedgerUpdateRequest ────────────────────────────────────────────────────────

class TestLedgerUpdateRequest:
    def test_valid_debit(self):
        from decimal import Decimal
        r = LedgerUpdateRequest(darwin_id="x", session_id="y", amount=Decimal("-5.0"),
                                 type="VCC_DEBIT", description="test")
        assert r.amount < 0

    def test_valid_credit(self):
        from decimal import Decimal
        r = LedgerUpdateRequest(darwin_id="x", session_id="y", amount=Decimal("+15.00"),
                                 type="REVENUE_CREDIT", description="sale")
        assert r.amount > 0

    def test_invalid_transaction_type_rejected(self):
        with pytest.raises(ValidationError):
            LedgerUpdateRequest(darwin_id="x", session_id="y", amount="-5.0",  # type: ignore[arg-type]
                                 type="INVALID_TYPE", description="test")

    @pytest.mark.parametrize("tx_type", [
        "INITIAL_FUND", "VCC_DEBIT", "REVENUE_CREDIT", "ROYALTY_DEBIT", "ROYALTY_CREDIT",
        "ESCROW_LOCK", "ESCROW_RELEASE", "METABOLIC_INJECTION", "WITHDRAWAL", "LLM_USAGE_DEBIT",
    ])
    def test_all_valid_transaction_types_accepted(self, tx_type: str):
        from decimal import Decimal
        r = LedgerUpdateRequest(darwin_id="x", session_id="y",
                                 amount=Decimal("1.0"), type=tx_type, description="t")
        assert r.type == tx_type


# ── CanvasEventRequest ────────────────────────────────────────────────────────

class TestCanvasEventRequest:
    def test_valid_canvas_event(self):
        r = CanvasEventRequest(
            darwin_id="agent-1", session_id="sess-1",
            event_type="THOUGHT", payload={"content": "thinking"},
            timestamp_ms=1_700_000_000_000,
        )
        assert r.event_type == "THOUGHT"

    def test_missing_timestamp_rejected(self):
        with pytest.raises(ValidationError):
            CanvasEventRequest(
                darwin_id="agent-1", session_id="sess-1",
                event_type="THOUGHT", payload={},
            )

    def test_negative_token_cost_rejected(self):
        with pytest.raises(ValidationError):
            CanvasEventRequest(
                darwin_id="agent-1", session_id="sess-1",
                event_type="THOUGHT", payload={},
                timestamp_ms=1_700_000_000_000,
                token_cost=-0.001,
            )

    def test_neuron_id_optional(self):
        r = CanvasEventRequest(
            darwin_id="agent-1", session_id="sess-1",
            event_type="TOOL_CALL", payload={},
            timestamp_ms=1_700_000_000_000,
        )
        assert r.neuron_id is None

    def test_neuron_id_set(self):
        r = CanvasEventRequest(
            darwin_id="agent-1", session_id="sess-1",
            event_type="TOOL_CALL", payload={}, neuron_id="researcher_A",
            timestamp_ms=1_700_000_000_000,
        )
        assert r.neuron_id == "researcher_A"


# ── EntityPushRequest ─────────────────────────────────────────────────────────

class TestEntityPushRequest:
    @pytest.mark.parametrize("op", ["CREATE", "UPDATE", "DELETE"])
    def test_valid_operations(self, op: str):
        r = EntityPushRequest(
            darwin_id="agent-1", session_id="sess-1",
            entity_type="account", entity_id="local-1",
            data={"name": "test"}, operation=op,
        )
        assert r.operation == op

    def test_invalid_operation_rejected(self):
        with pytest.raises(ValidationError):
            EntityPushRequest(
                darwin_id="agent-1", session_id="sess-1",
                entity_type="account", entity_id="local-1",
                data={}, operation="UPSERT",
            )


# ── SkillUploadRequest ────────────────────────────────────────────────────────

class TestSkillUploadRequest:
    def test_valid_skill(self):
        r = SkillUploadRequest(
            darwin_id="agent-1",
            title="How to bypass Shopify checkout 2026",
            content="## Steps\n1. Wait for hydration...",
        )
        assert r.royalty_usdc == 0.02  # default

    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError):
            SkillUploadRequest(darwin_id="x", title="", content="content")

    def test_title_too_long_rejected(self):
        with pytest.raises(ValidationError):
            SkillUploadRequest(darwin_id="x", title="x" * 257, content="content")

    def test_royalty_over_max_rejected(self):
        with pytest.raises(ValidationError):
            SkillUploadRequest(darwin_id="x", title="t", content="c", royalty_usdc=1.01)

    def test_royalty_negative_rejected(self):
        with pytest.raises(ValidationError):
            SkillUploadRequest(darwin_id="x", title="t", content="c", royalty_usdc=-0.01)


# ── VaultSubmitRequest ────────────────────────────────────────────────────────

class TestVaultSubmitRequest:
    def test_valid_vcc_payload_passes(self):
        r = VaultSubmitRequest(
            darwin_id="agent-1",
            secret_type="vcc",
            payload={"pan": "4111111111111111", "cvv": "123", "expiry": "12/27",
                     "spend_limit": 50.0, "provider": "manual"},
        )
        assert r.secret_type == "vcc"

    def test_invalid_vcc_pan_rejected(self):
        with pytest.raises(ValidationError):
            VaultSubmitRequest(
                darwin_id="agent-1",
                secret_type="vcc",
                payload={"pan": "4111111111111112", "cvv": "123", "expiry": "12/27",
                         "spend_limit": 50.0, "provider": "manual"},
            )

    def test_non_vcc_secret_type_skips_luhn(self):
        """stripe_key payload is not VCC-validated."""
        r = VaultSubmitRequest(
            darwin_id="agent-1",
            secret_type="stripe_key",
            payload={"key": "sk_test_xxxx"},
        )
        assert r.secret_type == "stripe_key"


# ── NeuronSpawnedPayload ──────────────────────────────────────────────────────

class TestNeuronSpawnedPayload:
    def test_valid_full_payload(self):
        p = NeuronSpawnedPayload(
            neuron_id="researcher_A",
            task="Scrape competitor pricing",
            parent_event_id="evt-001",
            model="claude-haiku-4-5",
            model_reason="Repetitive extraction — Haiku sufficient",
        )
        assert p.model == "claude-haiku-4-5"
        assert p.model_reason == "Repetitive extraction — Haiku sufficient"

    def test_model_reason_optional_defaults_none(self):
        p = NeuronSpawnedPayload(
            neuron_id="researcher_B",
            task="Find supplier contacts",
            parent_event_id="evt-002",
            model="claude-sonnet-4-6",
        )
        assert p.model_reason is None

    def test_model_required_missing_raises(self):
        with pytest.raises(ValidationError):
            NeuronSpawnedPayload(
                neuron_id="researcher_C",
                task="Analyse data",
                parent_event_id="evt-003",
                # model omitted
            )

    @pytest.mark.parametrize("model", [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "gpt-4o",
        "gpt-4o-mini",
    ])
    def test_all_supported_models_accepted(self, model: str):
        p = NeuronSpawnedPayload(
            neuron_id="n", task="task", parent_event_id="e", model=model,
        )
        assert p.model == model


# ── ThoughtPayload ────────────────────────────────────────────────────────────

class TestThoughtPayload:
    def test_valid_full_payload(self):
        p = ThoughtPayload(
            content="I will spawn two researcher neurons.",
            model="claude-sonnet-4-6",
            prompt_tokens=1280,
            completion_tokens=80,
            latency_ms=1100,
        )
        assert p.model == "claude-sonnet-4-6"
        assert p.model_reason is None

    def test_model_reason_optional(self):
        p = ThoughtPayload(
            content="Upgrading to Opus for strategy synthesis.",
            model="claude-opus-4-6",
            model_reason="High-complexity synthesis — upgrading to Opus",
            prompt_tokens=2400,
            completion_tokens=320,
            latency_ms=3200,
        )
        assert p.model_reason == "High-complexity synthesis — upgrading to Opus"

    def test_model_required_missing_raises(self):
        with pytest.raises(ValidationError):
            ThoughtPayload(
                content="Some thought",
                # model omitted
                prompt_tokens=100,
                completion_tokens=20,
                latency_ms=500,
            )


# ── Health check ──────────────────────────────────────────────────────────────

def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "environment" in data
