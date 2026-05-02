"""
Canvas service tests — event insertion, payload validation, and fan-out shape.

Every cognitive event from the agent lands in darwin_events.
Supabase Realtime then fans it out to the frontend Infinite Canvas.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.telemetry import VALID_EVENT_TYPES, CanvasEventRequest
from services.canvas_service import CanvasService
from tests.conftest import agent_headers, make_pool


# ── CanvasService unit tests ───────────────────────────────────────────────────

class TestCanvasServicePushEvent:
    @pytest.mark.asyncio
    async def test_push_event_returns_uuid(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "event-uuid-abc"}])
        svc = CanvasService(pool)
        result = await svc.push_event(
            darwin_id="agent-1",
            session_id="sess-1",
            event_type="THOUGHT",
            payload={"content": "thinking..."},
        )
        assert result == "event-uuid-abc"

    @pytest.mark.asyncio
    async def test_push_event_includes_correct_event_type(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-1"}])
        svc = CanvasService(pool)
        await svc.push_event("agent-1", "sess-1", "LEDGER_DEBIT", {"amount": -5.0})
        query, args = conn.fetchrow_calls[0]
        assert "INSERT INTO darwin_events" in query
        assert "LEDGER_DEBIT" in args

    @pytest.mark.asyncio
    async def test_push_event_serializes_payload_as_json(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-2"}])
        svc = CanvasService(pool)
        payload = {"tool_name": "browser_fetch", "status": "success"}
        await svc.push_event("agent-1", "sess-1", "TOOL_RESULT", payload)
        query, args = conn.fetchrow_calls[0]
        # Find the payload arg and verify it's valid JSON
        for arg in args:
            if isinstance(arg, str) and "tool_name" in arg:
                parsed = json.loads(arg)
                assert parsed["status"] == "success"

    @pytest.mark.asyncio
    async def test_push_event_sets_timestamp_ms(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-3"}])
        svc = CanvasService(pool)
        before = int(time.time() * 1000)
        await svc.push_event("agent-1", "sess-1", "THOUGHT", {})
        after = int(time.time() * 1000)
        _, args = conn.fetchrow_calls[0]
        ts = next(a for a in args if isinstance(a, int) and a > 1_000_000_000_000)
        assert before <= ts <= after + 100

    @pytest.mark.asyncio
    async def test_push_event_accepts_neuron_id(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-n"}])
        svc = CanvasService(pool)
        await svc.push_event("agent-1", "sess-1", "TOOL_CALL", {}, neuron_id="researcher_A")
        _, args = conn.fetchrow_calls[0]
        assert "researcher_A" in args

    @pytest.mark.asyncio
    async def test_push_neuron_spawned_sets_correct_event_type(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-ns"}])
        svc = CanvasService(pool)
        await svc.push_neuron_spawned("agent-1", "sess-1", "researcher_A", "scrape site", "parent-evt")
        _, args = conn.fetchrow_calls[0]
        assert "NEURON_SPAWNED" in args

    @pytest.mark.asyncio
    async def test_push_synaptic_integration_payload(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-si"}])
        svc = CanvasService(pool)
        await svc.push_synaptic_integration(
            "agent-1", "sess-1",
            merged=["researcher_A"], failed=["researcher_B"],
            synthesis="Formulating final strategy from 1/2 sources.",
        )
        _, args = conn.fetchrow_calls[0]
        payload_str = next(a for a in args if isinstance(a, str) and "researcher_A" in a)
        payload = json.loads(payload_str)
        assert payload["neuron_ids_merged"] == ["researcher_A"]
        assert payload["failed_neurons"] == ["researcher_B"]
        assert "synthesis" in payload

    @pytest.mark.asyncio
    async def test_push_event_with_token_cost(self):
        pool, conn = make_pool(fetchrow_returns=[{"id": "evt-cost"}])
        svc = CanvasService(pool)
        await svc.push_event("agent-1", "sess-1", "LLM_INFERENCE", {}, token_cost=0.00042)
        _, args = conn.fetchrow_calls[0]
        assert 0.00042 in args


# ── Valid event types ──────────────────────────────────────────────────────────

class TestValidEventTypes:
    @pytest.mark.parametrize("event_type", sorted(VALID_EVENT_TYPES))
    def test_all_valid_event_types_accepted(self, event_type: str):
        """Every event type in the DB CHECK constraint must be in the frozenset."""
        req = CanvasEventRequest(
            darwin_id="agent-1",
            session_id="sess-1",
            event_type=event_type,
            payload={"test": True},
            timestamp_ms=1_700_000_000_000,
        )
        assert req.event_type == event_type

    def test_invalid_event_type_rejected_at_route(self, client):
        res = client.post(
            "/api/v1/telemetry/stream-event",
            headers=agent_headers(),
            json={
                "darwin_id": "agent-test-uuid",
                "session_id": "session-test-uuid",
                "event_type": "MADE_UP_EVENT_TYPE",
                "payload": {},
                "timestamp_ms": 1_700_000_000_000,
            },
        )
        assert res.status_code in (422, 403)

    def test_lifecycle_events_present(self):
        for et in ("AGENT_SPAWNED", "AGENT_WOKE", "AGENT_HIBERNATED", "AGENT_TERMINATED"):
            assert et in VALID_EVENT_TYPES

    def test_cognition_events_present(self):
        for et in ("THOUGHT", "TOOL_CALL", "TOOL_RESULT", "LLM_INFERENCE"):
            assert et in VALID_EVENT_TYPES

    def test_finance_events_present(self):
        for et in ("LEDGER_DEBIT", "LEDGER_CREDIT", "VCC_INJECTED"):
            assert et in VALID_EVENT_TYPES

    def test_neuron_events_present(self):
        for et in ("NEURON_SPAWNED", "NEURON_COMPLETED", "NEURON_FAILED", "SYNAPTIC_INTEGRATION"):
            assert et in VALID_EVENT_TYPES

    def test_human_loop_events_present(self):
        for et in ("ASK_HUMAN_SENT", "ASK_HUMAN_RESOLVED", "APPROVAL_REQUESTED",
                   "APPROVAL_GRANTED", "APPROVAL_DENIED", "CAPTCHA_DELEGATED"):
            assert et in VALID_EVENT_TYPES


# ── Canvas route tests ─────────────────────────────────────────────────────────

class TestStreamEventRoute:
    def test_stream_event_requires_agent_jwt(self, client):
        res = client.post("/api/v1/telemetry/stream-event", json={})
        assert res.status_code in (401, 403, 422)

    def test_stream_event_wrong_agent_forbidden(self, client):
        """Agent A cannot push events for Agent B."""
        res = client.post(
            "/api/v1/telemetry/stream-event",
            headers=agent_headers(darwin_id="agent-A"),
            json={
                "darwin_id": "agent-B",
                "session_id": "s",
                "event_type": "THOUGHT",
                "payload": {},
                "timestamp_ms": 1_700_000_000_000,
            },
        )
        assert res.status_code == 403

    def test_stream_event_missing_timestamp_422(self, client):
        """Missing timestamp_ms must return 422 (auth provided so field validation runs)."""
        res = client.post(
            "/api/v1/telemetry/stream-event",
            headers=agent_headers(),
            json={"darwin_id": "agent-test-uuid", "session_id": "s",
                  "event_type": "THOUGHT", "payload": {}},
            # no timestamp_ms → pydantic 422
        )
        assert res.status_code == 422
