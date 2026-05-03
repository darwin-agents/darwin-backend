"""Tests for Langfuse Mental Replay trace fetching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest

from services.langfuse_service import (
    LangfuseService,
    _latency_ms,
    _maybe_filter_neuron,
    _obs_cost,
    _observation_to_node,
    _parse_iso_ms,
    langfuse_session_id,
)


def test_langfuse_session_id_matches_agent_template() -> None:
    assert langfuse_session_id("darwin-1", "sess-2") == "darwin-1::sess-2"


def test_observation_to_node_generation() -> None:
    node = _observation_to_node(
        {
            "id": "gen-1",
            "type": "GENERATION",
            "name": "ChatOpenAI",
            "parentObservationId": None,
            "startTime": "2024-06-01T12:00:00.000Z",
            "endTime": "2024-06-01T12:00:01.500Z",
            "input": {"prompt": "hi"},
            "output": {"text": "hello"},
            "metadata": {},
            "latency": 1.5,
            "calculatedTotalCost": 0.002,
        }
    )
    assert node["node_id"] == "gen-1"
    assert node["type"] == "LLM_INFERENCE"
    assert node["parent_node_id"] is None
    assert node["tool_name"] is None
    assert node["token_cost"] == 0.002
    assert node["latency_ms"] == 1500
    assert "in:" in node["content"] and "out:" in node["content"]


def test_maybe_filter_neuron_fallback_when_no_metadata() -> None:
    obs = [{"id": "1", "metadata": {}}]
    assert len(_maybe_filter_neuron(obs, "n1")) == 1


def test_maybe_filter_neuron_when_tagged() -> None:
    obs = [
        {"id": "a", "metadata": {"neuron_id": "n1"}},
        {"id": "b", "metadata": {"neuron_id": "n2"}},
    ]
    filtered = _maybe_filter_neuron(obs, "n1")
    assert [o["id"] for o in filtered] == ["a"]


@pytest.mark.asyncio
async def test_get_trace_returns_empty_without_credentials() -> None:
    settings = MagicMock()
    settings.langfuse_public_key.get_secret_value.return_value = ""
    settings.langfuse_secret_key.get_secret_value.return_value = ""
    settings.langfuse_host = "https://cloud.langfuse.com"

    redis = fakeredis.FakeRedis(decode_responses=True)
    svc = LangfuseService(settings, redis)
    out = await svc.get_trace("d1", "s1")
    assert out == []


@pytest.mark.asyncio
async def test_get_trace_fetches_via_httpx() -> None:
    settings = MagicMock()
    settings.langfuse_public_key.get_secret_value.return_value = "pk-test"
    settings.langfuse_secret_key.get_secret_value.return_value = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    redis = fakeredis.FakeRedis(decode_responses=True)

    class MockResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class MockClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> MockClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, params: dict | None = None) -> MockResponse:
            if params and "sessionId" in params:
                return MockResponse({"data": [{"id": "trace-a"}], "meta": {}})
            return MockResponse(
                {
                    "id": "trace-a",
                    "observations": [
                        {
                            "id": "o1",
                            "type": "GENERATION",
                            "name": None,
                            "parentObservationId": None,
                            "startTime": "2024-06-01T12:00:00.000Z",
                            "endTime": "2024-06-01T12:00:01.000Z",
                            "input": {},
                            "output": {"ok": True},
                            "metadata": {},
                            "latency": 1.0,
                            "calculatedTotalCost": 0.001,
                        }
                    ],
                }
            )

    svc = LangfuseService(settings, redis)
    with patch("services.langfuse_service.httpx.AsyncClient", MockClient):
        out = await svc.get_trace("darwin-x", "session-y")

    assert len(out) == 1
    assert out[0]["node_id"] == "o1"
    assert out[0]["type"] == "LLM_INFERENCE"

    cached = await redis.get("trace:lf:darwin-x:session-y:main")
    assert cached is not None


# ---------------------------------------------------------------------------
# _parse_iso_ms
# ---------------------------------------------------------------------------

def test_parse_iso_ms_valid_utc() -> None:
    ms = _parse_iso_ms("2024-06-01T12:00:00.000Z")
    assert ms is not None and ms > 0


def test_parse_iso_ms_none_returns_none() -> None:
    assert _parse_iso_ms(None) is None


def test_parse_iso_ms_invalid_string_returns_none() -> None:
    assert _parse_iso_ms("not-a-date") is None


def test_parse_iso_ms_non_string_returns_none() -> None:
    assert _parse_iso_ms(12345) is None


# ---------------------------------------------------------------------------
# _obs_cost
# ---------------------------------------------------------------------------

def test_obs_cost_calculated_total_cost() -> None:
    assert _obs_cost({"calculatedTotalCost": 0.005}) == 0.005


def test_obs_cost_total_price_fallback() -> None:
    assert _obs_cost({"totalPrice": 0.003}) == 0.003


def test_obs_cost_cost_details_dict() -> None:
    assert _obs_cost({"costDetails": {"total": 0.007}}) == 0.007


def test_obs_cost_missing_returns_zero() -> None:
    assert _obs_cost({}) == 0.0


# ---------------------------------------------------------------------------
# _latency_ms
# ---------------------------------------------------------------------------

def test_latency_ms_from_latency_field() -> None:
    assert _latency_ms({"latency": 2.5}) == 2500


def test_latency_ms_fallback_to_start_end() -> None:
    obs = {
        "startTime": "2024-06-01T12:00:00.000Z",
        "endTime": "2024-06-01T12:00:01.200Z",
    }
    result = _latency_ms(obs)
    assert 1190 <= result <= 1210


def test_latency_ms_missing_returns_zero() -> None:
    assert _latency_ms({}) == 0


# ---------------------------------------------------------------------------
# _observation_to_node — SPAN and EVENT types
# ---------------------------------------------------------------------------

def test_observation_to_node_span_maps_to_tool_call() -> None:
    node = _observation_to_node({
        "id": "span-1",
        "type": "SPAN",
        "name": "read_file",
        "parentObservationId": "gen-1",
        "startTime": "2024-06-01T12:00:00.000Z",
        "endTime": "2024-06-01T12:00:00.100Z",
        "input": {"path": "/tmp/x"},
        "output": {"content": "hello"},
        "metadata": {},
        "latency": 0.1,
    })
    assert node["type"] == "TOOL_CALL"
    assert node["tool_name"] == "read_file"
    assert node["tool_input"] == {"path": "/tmp/x"}
    assert node["tool_output"] == {"content": "hello"}
    assert node["parent_node_id"] == "gen-1"


def test_observation_to_node_event_maps_to_thought() -> None:
    node = _observation_to_node({
        "id": "evt-1",
        "type": "EVENT",
        "name": "thought",
        "parentObservationId": None,
        "startTime": "2024-06-01T12:00:02.000Z",
        "input": {"text": "I should search first"},
        "output": None,
        "metadata": {},
    })
    assert node["type"] == "THOUGHT"
    assert node["tool_name"] is None
    assert node["tool_input"] is None
    assert node["tool_output"] is None


def test_observation_to_node_unknown_type_falls_back_to_thought() -> None:
    node = _observation_to_node({"id": "x", "type": "CUSTOM"})
    assert node["type"] == "THOUGHT"


def test_observation_to_node_missing_id_uses_empty_string() -> None:
    node = _observation_to_node({"type": "EVENT"})
    assert node["node_id"] == ""


# ---------------------------------------------------------------------------
# _maybe_filter_neuron — neuron tag format
# ---------------------------------------------------------------------------

def test_maybe_filter_neuron_by_tag_list() -> None:
    obs = [
        {"id": "a", "metadata": {"tags": ["neuron:n1", "other"]}},
        {"id": "b", "metadata": {"tags": ["neuron:n2"]}},
    ]
    filtered = _maybe_filter_neuron(obs, "n1")
    assert [o["id"] for o in filtered] == ["a"]


def test_maybe_filter_neuron_empty_neuron_id_returns_all() -> None:
    obs = [{"id": "a", "metadata": {}}, {"id": "b", "metadata": {}}]
    assert _maybe_filter_neuron(obs, "") == obs
    assert _maybe_filter_neuron(obs, None) == obs


def test_maybe_filter_neuron_no_match_returns_all() -> None:
    """Fallback: if neuron_id matches nothing, return full list."""
    obs = [{"id": "a", "metadata": {"neuron_id": "n99"}}]
    result = _maybe_filter_neuron(obs, "n-unknown")
    assert result == obs


# ---------------------------------------------------------------------------
# Cache hit path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_trace_cache_hit_skips_http() -> None:
    settings = MagicMock()
    settings.langfuse_public_key.get_secret_value.return_value = "pk-test"
    settings.langfuse_secret_key.get_secret_value.return_value = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    redis = fakeredis.FakeRedis(decode_responses=True)
    cached_nodes = [{"node_id": "cached-1", "type": "THOUGHT"}]
    import json
    await redis.set("trace:lf:darwin-c:session-c:main", json.dumps(cached_nodes))

    svc = LangfuseService(settings, redis)
    with patch("services.langfuse_service.httpx.AsyncClient") as mock_client:
        out = await svc.get_trace("darwin-c", "session-c")

    mock_client.assert_not_called()
    assert out == cached_nodes


# ---------------------------------------------------------------------------
# GET /darwins/{darwin_id}/logs — route auth
# ---------------------------------------------------------------------------

def test_logs_route_requires_auth(client) -> None:
    res = client.get("/api/v1/darwins/some-darwin-id/logs?session_id=sess-1")
    assert res.status_code in (401, 403)


def test_logs_route_accepts_human_jwt(client, app) -> None:
    from unittest.mock import AsyncMock as _AsyncMock
    from tests.conftest import human_headers

    fake_redis = MagicMock()
    fake_redis.aclose = _AsyncMock(return_value=None)

    async def _fake_get_trace(*a, **kw):
        return []

    mock_svc = MagicMock()
    mock_svc.get_trace = _fake_get_trace

    with (
        patch("routers.darwins.LangfuseService", return_value=mock_svc),
        patch("routers.darwins.aioredis.from_url", return_value=fake_redis),
    ):
        res = client.get(
            "/api/v1/darwins/some-darwin-id/logs?session_id=sess-1",
            headers=human_headers(),
        )

    assert res.status_code == 200
    assert "trace" in res.json()
