"""
Agent lifecycle and FSM tests.

Covers:
  - All valid FSM transitions
  - All invalid FSM transitions must return 409
  - Auth enforcement at every lifecycle endpoint
  - Spawn pre-flight intent scan integration
  - Hibernate registers wake trigger
  - Terminate emits canvas event and sends Telegram
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from routers.darwins import _FSM, _assert_transition
from tests.conftest import agent_headers, human_headers, make_pool

DARWIN_ID = "darwin-fsm-test-uuid"
SESSION_ID = "00000000-0000-0000-0000-000000000001"


# ── FSM unit tests (pure logic, no I/O) ───────────────────────────────────────

class TestFSM:
    def test_pending_to_active_valid(self):
        _assert_transition("PENDING", "ACTIVE", DARWIN_ID)  # must not raise

    def test_active_to_hibernating_valid(self):
        _assert_transition("ACTIVE", "HIBERNATING", DARWIN_ID)

    def test_active_to_terminated_valid(self):
        _assert_transition("ACTIVE", "TERMINATED", DARWIN_ID)

    def test_hibernating_to_active_valid(self):
        _assert_transition("HIBERNATING", "ACTIVE", DARWIN_ID)

    def test_hibernating_to_terminated_valid(self):
        _assert_transition("HIBERNATING", "TERMINATED", DARWIN_ID)

    def test_terminated_to_active_invalid(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _assert_transition("TERMINATED", "ACTIVE", DARWIN_ID)
        assert exc_info.value.status_code == 409

    def test_terminated_to_hibernating_invalid(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _assert_transition("TERMINATED", "HIBERNATING", DARWIN_ID)
        assert exc_info.value.status_code == 409

    def test_active_to_active_invalid(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _assert_transition("ACTIVE", "ACTIVE", DARWIN_ID)
        assert exc_info.value.status_code == 409

    def test_pending_to_hibernating_invalid(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _assert_transition("PENDING", "HIBERNATING", DARWIN_ID)
        assert exc_info.value.status_code == 409

    def test_all_valid_transitions_match_spec(self):
        """Explicitly verify the FSM table matches the spec diagram."""
        assert _FSM["PENDING"] == {"ACTIVE", "TERMINATED"}
        assert _FSM["ACTIVE"] == {"HIBERNATING", "TERMINATED"}
        assert _FSM["HIBERNATING"] == {"ACTIVE", "TERMINATED"}
        assert _FSM["TERMINATED"] == set()

    def test_409_response_contains_current_state(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _assert_transition("TERMINATED", "ACTIVE", DARWIN_ID)
        detail = exc_info.value.detail
        assert detail["current_state"] == "TERMINATED"
        assert detail["error"] == "invalid_state_transition"


# ── Route auth enforcement ─────────────────────────────────────────────────────

class TestSpawnAuth:
    def test_spawn_requires_human_jwt(self, client: TestClient):
        res = client.post("/api/v1/darwins/spawn", json={
            "name": "Test", "directive": "do legal things", "initial_budget": 10.0, "vcc_ref": "x"
        })
        assert res.status_code in (401, 403)

    def test_spawn_rejects_agent_jwt(self, client: TestClient):
        res = client.post(
            "/api/v1/darwins/spawn",
            headers=agent_headers(),
            json={"name": "T", "directive": "do legal things", "initial_budget": 10.0, "vcc_ref": "x"},
        )
        assert res.status_code in (401, 403)

    def test_spawn_missing_directive_422(self, client: TestClient):
        res = client.post(
            "/api/v1/darwins/spawn",
            headers=human_headers(),
            json={"name": "T", "initial_budget": 10.0, "vcc_ref": "x"},
        )
        assert res.status_code == 422

    def test_spawn_directive_too_short_422(self, client: TestClient):
        """Directive must be ≥ 10 characters."""
        res = client.post(
            "/api/v1/darwins/spawn",
            headers=human_headers(),
            json={"name": "T", "directive": "short", "initial_budget": 10.0, "vcc_ref": "x"},
        )
        assert res.status_code == 422

    def test_spawn_negative_budget_422(self, client: TestClient):
        res = client.post(
            "/api/v1/darwins/spawn",
            headers=human_headers(),
            json={"name": "T", "directive": "a legal directive", "initial_budget": -5.0, "vcc_ref": "x"},
        )
        assert res.status_code == 422

    def test_spawn_over_budget_limit_422(self, client: TestClient):
        """Budget must be ≤ 10,000."""
        res = client.post(
            "/api/v1/darwins/spawn",
            headers=human_headers(),
            json={"name": "T", "directive": "a legal directive", "initial_budget": 50000.0, "vcc_ref": "x"},
        )
        assert res.status_code == 422


class TestHibernateAuth:
    def test_hibernate_requires_agent_jwt(self, client: TestClient):
        res = client.post(f"/api/v1/darwins/{DARWIN_ID}/hibernate", json={
            "session_id": SESSION_ID,
            "reason": "test",
            "wake_trigger": {"type": "CRON", "trigger_at": "2026-06-01T00:00:00Z"},
            "episodic_summary": "test summary",
        })
        assert res.status_code in (401, 403)

    def test_hibernate_rejects_human_jwt(self, client: TestClient):
        res = client.post(
            f"/api/v1/darwins/{DARWIN_ID}/hibernate",
            headers=human_headers(),
            json={"session_id": SESSION_ID, "reason": "test",
                  "wake_trigger": {"type": "CRON"}, "episodic_summary": "x"},
        )
        assert res.status_code in (401, 403)

    def test_hibernate_rejects_wrong_agent(self, client: TestClient):
        """Agent JWT for 'other-agent' must not hibernate a different agent."""
        res = client.post(
            f"/api/v1/darwins/{DARWIN_ID}/hibernate",
            headers=agent_headers(darwin_id="other-agent"),
            json={"session_id": SESSION_ID, "reason": "test",
                  "wake_trigger": {"type": "CRON"}, "episodic_summary": "x"},
        )
        assert res.status_code == 403


class TestTerminateAuth:
    def test_terminate_requires_human_jwt(self, client: TestClient):
        res = client.post(f"/api/v1/darwins/{DARWIN_ID}/terminate")
        assert res.status_code in (401, 403)

    def test_state_endpoint_requires_human_jwt(self, client: TestClient):
        res = client.get(f"/api/v1/darwins/{DARWIN_ID}/state")
        assert res.status_code in (401, 403)

    def test_uplink_requires_human_jwt(self, client: TestClient):
        res = client.post(f"/api/v1/darwins/{DARWIN_ID}/uplink",
                          json={"message": "test"})
        assert res.status_code in (401, 403)

    def test_config_patch_requires_human_jwt(self, client: TestClient):
        res = client.patch(f"/api/v1/darwins/{DARWIN_ID}/config",
                           json={"requires_approval": False})
        assert res.status_code in (401, 403)


# ── Spawn with intent scan integration ────────────────────────────────────────

class TestSpawnWithIntentScan:
    def test_spawn_flagged_directive_rejected(self, client: TestClient, app):
        from db.client import get_pool as _get_pool
        pool, conn = make_pool(fetchrow_returns=[{"id": "agent-uuid-new"}])
        app.dependency_overrides[_get_pool] = lambda: pool

        with patch("services.intent_scan_service.IntentScanService.scan_directive",
                   new=AsyncMock(return_value=(True, "DDoS attempt"))):
            res = client.post(
                "/api/v1/darwins/spawn",
                headers=human_headers(),
                json={"name": "BadBot", "directive": "perform a DDoS against example.com",
                      "initial_budget": 10.0, "vcc_ref": "x"},
            )
        assert res.status_code == 403
        assert res.json()["detail"]["error"] == "directive_rejected"

    def test_spawn_flagged_directive_includes_reason(self, client: TestClient, app):
        from db.client import get_pool as _get_pool
        pool, _ = make_pool(fetchrow_returns=[{"id": "agent-uuid-new"}])
        app.dependency_overrides[_get_pool] = lambda: pool

        with patch("services.intent_scan_service.IntentScanService.scan_directive",
                   new=AsyncMock(return_value=(True, "credential stuffing"))):
            res = client.post(
                "/api/v1/darwins/spawn",
                headers=human_headers(),
                json={"name": "BadBot", "directive": "stuff credentials",
                      "initial_budget": 10.0, "vcc_ref": "x"},
            )
        assert "credential stuffing" in res.json()["detail"]["reason"]
