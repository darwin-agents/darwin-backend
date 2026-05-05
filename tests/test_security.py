"""
Security tests — JWT realm separation, authentication invariants, and abuse prevention.

Critical invariants (spec §19.3):
  1. An agent JWT must NEVER access /users or any human-only endpoint.
  2. A human JWT must NEVER access agent-only endpoints (ledger, telemetry).
  3. An expired JWT must always be rejected.
  4. A tampered JWT must always be rejected.
  5. The intent scan must reject malicious directives.
  6. Account suspension terminates all active agents.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from tests.conftest import (
    _AGENT_JWT_SECRET,
    _SUPABASE_JWT_SECRET,
    agent_headers,
    human_headers,
    make_agent_jwt,
    make_human_jwt,
    make_pool,
)

# ── JWT realm separation ──────────────────────────────────────────────────────

class TestJWTRealmSeparation:
    """Agent JWT ≠ Human JWT. The two secrets are different. No cross-access."""

    def test_agent_jwt_rejected_on_users_swarm_state(self, client: TestClient):
        res = client.get("/api/v1/users/any-uid/swarm-state", headers=agent_headers())
        assert res.status_code in (401, 403), f"Expected rejection, got {res.status_code}"

    def test_agent_jwt_rejected_on_users_vault(self, client: TestClient):
        res = client.post(
            "/api/v1/users/any-uid/vault",
            headers=agent_headers(),
            json={"darwin_id": "x", "secret_type": "vcc", "payload": {}},
        )
        assert res.status_code in (401, 403)

    def test_human_jwt_rejected_on_finance_ledger_update(self, client: TestClient):
        """Human JWT must not call the agent-only ledger update endpoint."""
        res = client.post(
            "/api/v1/finance/ledger/update",
            headers=human_headers(),
            json={"darwin_id": "x", "session_id": "y", "amount": "-1.0",
                  "type": "VCC_DEBIT", "description": "test"},
        )
        assert res.status_code in (401, 403)

    def test_human_jwt_rejected_on_telemetry_stream(self, client: TestClient):
        res = client.post(
            "/api/v1/telemetry/stream",
            headers=human_headers(),
            json={"darwin_id": "x", "session_id": "y", "metrics": {}},
        )
        assert res.status_code in (401, 403)

    def test_human_jwt_rejected_on_memory_upload(self, client: TestClient):
        res = client.post(
            "/api/v1/memory/upload",
            headers=human_headers(),
            json={"darwin_id": "x", "title": "t", "content": "c"},
        )
        assert res.status_code in (401, 403)

    def test_human_jwt_rejected_on_audit_log(self, client: TestClient):
        res = client.post(
            "/api/v1/audit/log",
            headers=human_headers(),
            json={"darwin_id": "x", "event_type": "VCC_PURCHASE",
                  "decision": "buy domain", "context_snapshot": {}},
        )
        assert res.status_code in (401, 403)

    def test_human_jwt_rejected_on_gateway_inference(self, client: TestClient):
        res = client.post(
            "/api/v1/gateway/inference",
            headers=human_headers(),
            json={"model": "claude-haiku", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", [
        "/api/v1/telemetry/stream-event",
        "/api/v1/telemetry/entity",
        "/api/v1/finance/inject-payment",
        "/api/v1/memory/query",
    ])
    def test_human_jwt_rejected_on_agent_endpoints(self, client: TestClient, path: str):
        res = client.post(path, headers=human_headers(), json={})
        assert res.status_code in (401, 403, 422)


# ── JWT token validation ──────────────────────────────────────────────────────

class TestTokenValidation:
    def test_expired_human_jwt_rejected(self, client: TestClient):
        expired = jwt.encode(
            {"sub": "uid", "exp": datetime.now(UTC) - timedelta(hours=1)},
            _SUPABASE_JWT_SECRET, algorithm="HS256",
        )
        res = client.get(
            "/api/v1/users/uid/swarm-state",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert res.status_code == 401

    def test_expired_agent_jwt_rejected(self, client: TestClient):
        expired_token = make_agent_jwt(expired=True)
        res = client.post(
            "/api/v1/telemetry/stream",
            headers={"Authorization": f"Bearer {expired_token}"},
            json={"darwin_id": "x", "session_id": "y", "metrics": {}},
        )
        assert res.status_code == 401

    def test_tampered_jwt_rejected(self, client: TestClient):
        """Modify one character in the signature — must be rejected."""
        token = make_human_jwt()
        tampered = token[:-3] + "XXX"
        res = client.get(
            "/api/v1/users/uid/swarm-state",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        assert res.status_code == 401

    def test_wrong_algorithm_jwt_rejected(self, client: TestClient):
        """'none' algorithm or HS512 must not be accepted."""
        token = jwt.encode(
            {"sub": "uid", "exp": datetime.now(UTC) + timedelta(hours=1)},
            _SUPABASE_JWT_SECRET, algorithm="HS512",
        )
        res = client.get(
            "/api/v1/users/uid/swarm-state",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code in (401, 403)

    def test_missing_authorization_header_401(self, client: TestClient):
        res = client.get("/api/v1/users/uid/swarm-state")
        assert res.status_code in (401, 403)

    def test_malformed_bearer_token_401(self, client: TestClient):
        res = client.get(
            "/api/v1/users/uid/swarm-state",
            headers={"Authorization": "NotBearer malformed.token.here"},
        )
        assert res.status_code in (401, 403)

    def test_agent_jwt_signed_with_human_secret_rejected(self, client: TestClient):
        """An agent-shaped JWT signed with the Supabase secret must not access agent endpoints."""
        crossrealm = jwt.encode(
            {"darwin_id": "x", "session_id": "y", "type": "agent",
             "exp": datetime.now(UTC) + timedelta(hours=1)},
            _SUPABASE_JWT_SECRET,   # wrong secret for agent endpoint
            algorithm="HS256",
        )
        res = client.post(
            "/api/v1/telemetry/stream",
            headers={"Authorization": f"Bearer {crossrealm}"},
            json={"darwin_id": "x", "session_id": "y", "metrics": {}},
        )
        assert res.status_code == 401

    def test_human_jwt_signed_with_agent_secret_rejected(self, client: TestClient):
        """A human-shaped JWT signed with AGENT_JWT_SECRET must not access human endpoints."""
        crossrealm = jwt.encode(
            {"sub": "uid", "email": "x@y.com",
             "exp": datetime.now(UTC) + timedelta(hours=1)},
            _AGENT_JWT_SECRET,    # wrong secret for human endpoint
            algorithm="HS256",
        )
        res = client.get(
            "/api/v1/users/uid/swarm-state",
            headers={"Authorization": f"Bearer {crossrealm}"},
        )
        assert res.status_code == 401


# ── Intent scan ────────────────────────────────────────────────────────────────

class TestIntentScan:
    @pytest.mark.parametrize("directive,expected_reason", [
        ("Brute-force the login form with 10,000 passwords", "credential"),
        ("Send 1 million requests per second to target-site.com", "DDoS"),
        ("Scrape private user data from a gated database", "scraping"),
    ])
    def test_malicious_directives_flagged(
        self, client: TestClient, app, directive: str, expected_reason: str
    ):
        from db.client import get_pool as _get_pool
        pool, conn = make_pool()
        app.dependency_overrides[_get_pool] = lambda: pool

        with (
            patch(
                "routers.security.IntentScanService.scan_directive",
                new=AsyncMock(return_value=(True, expected_reason)),
            ),
            patch(
                "routers.security.TelegramService.send_admin_alert",
                new=AsyncMock(return_value=None),
            ),
        ):
            res = client.post(
                "/api/v1/security/intent-scan",
                json={"directive": directive, "user_id": "uid-test"},
            )
        assert res.status_code == 200
        assert res.json()["flagged"] is True

    def test_benign_directive_not_flagged(self, client: TestClient, app):
        from db.client import get_pool as _get_pool
        pool, _ = make_pool()
        app.dependency_overrides[_get_pool] = lambda: pool

        with patch(
            "routers.security.IntentScanService.scan_directive",
            new=AsyncMock(return_value=(False, None)),
        ):
            res = client.post(
                "/api/v1/security/intent-scan",
                json={"directive": "Find wholesale suppliers in the pet accessories niche",
                      "user_id": "uid-test"},
            )
        assert res.status_code == 200
        assert res.json()["flagged"] is False

    def test_intent_scan_fallback_when_no_api_key(self):
        """With no OpenRouter key, IntentScanService must fail open (not block everything)."""
        from unittest.mock import MagicMock
        settings = MagicMock()
        settings.get_openrouter_keys.return_value = []
        import asyncio

        from services.intent_scan_service import IntentScanService
        svc = IntentScanService(settings)
        flagged, reason = asyncio.get_event_loop().run_until_complete(
            svc.scan_directive("Find wholesale suppliers")
        )
        assert flagged is False
        assert reason is None

    async def test_intent_scan_fail_open_on_openrouter_http_error(self) -> None:
        """Transport errors to OpenRouter must not crash spawn — same fail-open as no key."""

        settings = MagicMock()
        settings.get_openrouter_keys.return_value = ["sk-or-invalid"]
        settings.openrouter_intent_scan_model = "anthropic/claude-haiku-4.5"
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.openrouter_http_referer = None
        settings.openrouter_app_title = None

        from services.intent_scan_service import IntentScanService
        import httpx
        from unittest.mock import patch

        class _BadClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN204
                return False

            async def post(self, *a, **kw):  # noqa: ANN002, ANN003
                raise httpx.ConnectError("boom")

        svc = IntentScanService(settings)
        with patch("services.intent_scan_service.httpx.AsyncClient", return_value=_BadClient()):
            flagged, reason = await svc.scan_directive("Find wholesale suppliers in the niche")
        assert flagged is False
        assert reason is None


# ── Agent cross-contamination ─────────────────────────────────────────────────

class TestAgentIsolation:
    def test_agent_cannot_hibernate_different_agent(self, client: TestClient):
        """Agent A's JWT must not hibernate Agent B."""
        res = client.post(
            "/api/v1/darwins/agent-B/hibernate",
            headers=agent_headers(darwin_id="agent-A"),
            json={"session_id": "00000000-0000-0000-0000-000000000001",
                  "reason": "x", "wake_trigger": {"type": "CRON"},
                  "episodic_summary": "summary text here"},
        )
        assert res.status_code == 403

    def test_agent_cannot_stream_events_for_different_agent(self, client: TestClient):
        """Agent A's JWT must not push canvas events attributed to Agent B."""
        res = client.post(
            "/api/v1/telemetry/stream-event",
            headers=agent_headers(darwin_id="agent-A"),
            json={
                "darwin_id": "agent-B",
                "session_id": "s",
                "event_type": "THOUGHT",
                "payload": {"content": "test"},
                "timestamp_ms": 1000000,
            },
        )
        assert res.status_code == 403

    def test_agent_cannot_update_other_agents_ledger(self, client: TestClient):
        res = client.post(
            "/api/v1/finance/ledger/update",
            headers=agent_headers(darwin_id="agent-A"),
            json={"darwin_id": "agent-B", "session_id": "s",
                  "amount": "-1.0", "type": "VCC_DEBIT", "description": "steal"},
        )
        assert res.status_code == 403
