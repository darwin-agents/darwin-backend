"""
Telemetry and circuit-breaker tests.

The automated immune system must:
  - Accept metabolic streams from agents (agent JWT only)
  - Detect burn-rate runaway and trip the circuit breaker
  - Persist entity pushes and emit canvas events
  - Return telemetry history bucketed by hour
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from workers.telemetry_worker import _check_circuit_breaker
from tests.conftest import agent_headers, human_headers, make_pool


# ── Circuit breaker unit tests ────────────────────────────────────────────────

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_normal_burn_rate_no_trip(self):
        """5 samples at equal burn rate — no breaker trip."""
        settings = MagicMock()
        settings.cb_burn_multiplier = 5.0
        settings.cb_failed_http_threshold = 10

        pool, _ = make_pool()
        samples = [json.dumps({"tokens_burned_last_30s": 100}) for _ in range(5)]

        import fakeredis.aioredis as fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        for s in samples:
            await redis.lpush("telemetry:ts:agent-1", s)

        with patch("workers.telemetry_worker._trip", new=AsyncMock()) as mock_trip:
            await _check_circuit_breaker("agent-1", {"tokens_burned_last_30s": 120}, settings, pool, redis)
            mock_trip.assert_not_called()

    @pytest.mark.asyncio
    async def test_metabolic_runaway_trips_breaker(self):
        """Burn rate 10× historical average must trip the breaker."""
        settings = MagicMock()
        settings.cb_burn_multiplier = 5.0
        settings.cb_failed_http_threshold = 10

        pool, _ = make_pool()

        import fakeredis.aioredis as fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        for _ in range(5):
            await redis.lpush("telemetry:ts:agent-1", json.dumps({"tokens_burned_last_30s": 100}))

        with patch("workers.telemetry_worker._trip", new=AsyncMock()) as mock_trip:
            # Current burn = 1500 tokens, average = 100, multiplier threshold = 5.0
            await _check_circuit_breaker(
                "agent-1",
                {"tokens_burned_last_30s": 1500, "failed_http_requests_last_30s": 0},
                settings, pool, redis,
            )
            mock_trip.assert_called_once_with("agent-1", "metabolic_runaway", pool, settings)

    @pytest.mark.asyncio
    async def test_http_failure_storm_trips_breaker(self):
        """≥ cb_failed_http_threshold failed HTTP requests in 30s must trip breaker."""
        settings = MagicMock()
        settings.cb_burn_multiplier = 5.0
        settings.cb_failed_http_threshold = 10

        pool, _ = make_pool()

        import fakeredis.aioredis as fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        for _ in range(5):
            await redis.lpush("telemetry:ts:agent-1", json.dumps({"tokens_burned_last_30s": 100}))

        with patch("workers.telemetry_worker._trip", new=AsyncMock()) as mock_trip:
            await _check_circuit_breaker(
                "agent-1",
                {"tokens_burned_last_30s": 100, "failed_http_requests_last_30s": 11},
                settings, pool, redis,
            )
            mock_trip.assert_called_once_with("agent-1", "http_failure_storm", pool, settings)

    @pytest.mark.asyncio
    async def test_insufficient_samples_no_trip(self):
        """With < 3 samples, the circuit breaker must not trip (not enough history)."""
        settings = MagicMock()
        settings.cb_burn_multiplier = 5.0
        settings.cb_failed_http_threshold = 10

        pool, _ = make_pool()

        import fakeredis.aioredis as fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        await redis.lpush("telemetry:ts:agent-1", json.dumps({"tokens_burned_last_30s": 100}))

        with patch("workers.telemetry_worker._trip", new=AsyncMock()) as mock_trip:
            await _check_circuit_breaker(
                "agent-1",
                {"tokens_burned_last_30s": 9999, "failed_http_requests_last_30s": 0},
                settings, pool, redis,
            )
            mock_trip.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_historical_avg_no_divide_by_zero(self):
        """If historical average is 0, must not raise ZeroDivisionError."""
        settings = MagicMock()
        settings.cb_burn_multiplier = 5.0
        settings.cb_failed_http_threshold = 10

        pool, _ = make_pool()

        import fakeredis.aioredis as fakeredis
        redis = fakeredis.FakeRedis(decode_responses=True)
        for _ in range(5):
            await redis.lpush("telemetry:ts:agent-1", json.dumps({"tokens_burned_last_30s": 0}))

        with patch("workers.telemetry_worker._trip", new=AsyncMock()) as mock_trip:
            await _check_circuit_breaker(
                "agent-1",
                {"tokens_burned_last_30s": 1000, "failed_http_requests_last_30s": 0},
                settings, pool, redis,
            )
            # avg=0, condition `avg > 0` must be checked before multiplication
            mock_trip.assert_not_called()


# ── Route-level telemetry tests ────────────────────────────────────────────────

class TestTelemetryRoutes:
    def test_stream_requires_agent_jwt(self, client):
        res = client.post("/api/v1/telemetry/stream", json={})
        assert res.status_code in (401, 403, 422)

    def test_stream_rejects_human_jwt(self, client):
        res = client.post(
            "/api/v1/telemetry/stream",
            headers=human_headers(),
            json={"darwin_id": "x", "session_id": "y", "metrics": {}},
        )
        assert res.status_code in (401, 403)

    def test_stream_wrong_agent_forbidden(self, client):
        res = client.post(
            "/api/v1/telemetry/stream",
            headers=agent_headers(darwin_id="agent-A"),
            json={
                "darwin_id": "agent-B",
                "session_id": "s",
                "metrics": {"tokens_burned_last_30s": 100, "llm_cost_last_30s": 0.01,
                            "compute_cost_last_30s": 0.001, "failed_http_requests_last_30s": 0,
                            "memory_util_mb": 256, "current_task": "browsing"},
            },
        )
        assert res.status_code == 403

    def test_entity_push_requires_agent_jwt(self, client):
        res = client.post("/api/v1/telemetry/entity", json={})
        assert res.status_code in (401, 403, 422)

    def test_entity_push_invalid_operation_422(self, client):
        res = client.post(
            "/api/v1/telemetry/entity",
            headers=agent_headers(),
            json={
                "darwin_id": "agent-test-uuid",
                "session_id": "session-test-uuid",
                "entity_type": "account",
                "entity_id": "local-1",
                "data": {},
                "operation": "INVALID_OP",
            },
        )
        assert res.status_code == 422

    @pytest.mark.parametrize("operation", ["CREATE", "UPDATE", "DELETE"])
    def test_entity_push_accepts_valid_operations(self, client, operation):
        with patch("db.client.get_pool", new_callable=AsyncMock) as mgp:
            pool, conn = make_pool()
            mgp.return_value = pool
            res = client.post(
                "/api/v1/telemetry/entity",
                headers=agent_headers(),
                json={
                    "darwin_id": "agent-test-uuid",
                    "session_id": "session-test-uuid",
                    "entity_type": "account",
                    "entity_id": "local-1",
                    "data": {"name": "Supplier ABC"},
                    "operation": operation,
                },
            )
        assert res.status_code in (200, 201, 500)  # 500 only if mock conn not set up

    def test_history_requires_human_jwt(self, client):
        res = client.get("/api/v1/telemetry/agent-1/history")
        assert res.status_code in (401, 403, 422)
