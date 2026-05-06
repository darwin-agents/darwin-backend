"""
LangfuseService — fetch structured thought logs (observations) for Mental Replay.

Uses the Langfuse public REST API with the same session naming as Hermes
(`darwin_id::session_id` from `config/config.yaml.j2` langfuse.session_name).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote

import httpx
import redis.asyncio as aioredis

from config import Settings

log = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_TRACE_LIST_LIMIT = 100


def langfuse_session_id(darwin_id: str, session_id: str) -> str:
    """Must match Hermes template `langfuse.session_name: "{{ darwin_id }}::{{ session_id }}"`."""
    return f"{darwin_id}::{session_id}"


class LangfuseService:
    def __init__(self, settings: Settings, redis: aioredis.Redis) -> None:
        self._public = settings.langfuse_public_key.get_secret_value()
        self._secret = settings.langfuse_secret_key.get_secret_value()
        host = (settings.langfuse_host or "").strip().rstrip("/") or "https://cloud.langfuse.com"
        self._base = f"{host}/api/public"
        self._redis = redis

    async def get_trace(
        self,
        darwin_id: str,
        session_id: str,
        neuron_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch and cache the cognitive trace for a session.
        Returns trace nodes sorted by start time (same shape as the former LangSmith path).
        """
        cache_key = f"trace:lf:{darwin_id}:{session_id}:{neuron_id or 'main'}"
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                return cast(list[dict[str, Any]], json.loads(cached))
        except Exception as exc:
            log.debug("Redis cache read unavailable for trace %s — %s", cache_key, exc)

        if not self._public or not self._secret:
            return []

        try:
            nodes = await self._fetch_trace_nodes(darwin_id, session_id, neuron_id)
        except Exception as exc:
            log.warning("LangfuseService.get_trace failed for %s::%s — %s", darwin_id, session_id, exc)
            nodes = []

        try:
            await self._redis.set(cache_key, json.dumps(nodes), ex=_CACHE_TTL)
        except Exception as exc:
            log.debug("Redis cache write unavailable for trace %s — %s", cache_key, exc)

        return nodes

    async def _fetch_trace_nodes(
        self,
        darwin_id: str,
        session_id: str,
        neuron_id: str | None,
    ) -> list[dict[str, Any]]:
        lf_session = langfuse_session_id(darwin_id, session_id)
        auth = (self._public, self._secret)
        async with httpx.AsyncClient(timeout=20, auth=auth) as client:
            trace_ids = await self._list_trace_ids(client, lf_session)
            observations: list[dict[str, Any]] = []
            for tid in trace_ids:
                observations.extend(await self._fetch_trace_observations(client, tid))

        observations.sort(key=lambda o: _parse_iso_ms(o.get("startTime")) or 0)
        observations = _maybe_filter_neuron(observations, neuron_id)
        return [_observation_to_node(o) for o in observations]

    async def _list_trace_ids(self, client: httpx.AsyncClient, lf_session: str) -> list[str]:
        params: dict[str, str | int] = {
            "sessionId": lf_session,
            "limit": _TRACE_LIST_LIMIT,
            "orderBy": "timestamp.asc",
        }
        r = await client.get(f"{self._base}/traces", params=params)
        r.raise_for_status()
        payload = r.json()

        traces = payload.get("data") or []
        return [t["id"] for t in traces if isinstance(t, dict) and t.get("id")]

    async def _fetch_trace_observations(
        self, client: httpx.AsyncClient, trace_id: str
    ) -> list[dict[str, Any]]:
        r = await client.get(f"{self._base}/traces/{quote(trace_id, safe='')}")
        r.raise_for_status()
        data = r.json()
        raw = data.get("observations") or []
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
        return out


def _parse_iso_ms(value: Any) -> int | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError):
        return None


def _maybe_filter_neuron(
    observations: list[dict[str, Any]], neuron_id: str | None
) -> list[dict[str, Any]]:
    if not neuron_id or not neuron_id.strip():
        return observations
    nid = neuron_id.strip()
    tagged = [o for o in observations if _obs_matches_neuron(o, nid)]
    return tagged if tagged else observations


def _obs_matches_neuron(obs: dict[str, Any], neuron_id: str) -> bool:
    md = obs.get("metadata")
    if not isinstance(md, dict):
        return False
    if md.get("neuron_id") == neuron_id or md.get("neuron") == neuron_id:
        return True
    tags = md.get("tags")
    return bool(isinstance(tags, list) and f"neuron:{neuron_id}" in tags)


def _observation_to_node(obs: dict[str, Any]) -> dict[str, Any]:
    otype = str(obs.get("type") or "").upper()
    name = obs.get("name")
    tool_name = str(name) if name else None

    if otype == "GENERATION":
        node_type = "LLM_INFERENCE"
        tool_name = None
    elif otype == "SPAN":
        node_type = "TOOL_CALL"
    elif otype == "EVENT":
        node_type = "THOUGHT"
    else:
        node_type = "THOUGHT"

    content = _format_content(obs.get("input"), obs.get("output"))

    latency_ms = _latency_ms(obs)
    token_cost = _obs_cost(obs)

    ti, to = _tool_io(obs, node_type)

    return {
        "node_id": str(obs.get("id") or ""),
        "parent_node_id": obs.get("parentObservationId"),
        "type": node_type,
        "timestamp_ms": _parse_iso_ms(obs.get("startTime")) or 0,
        "content": content,
        "tool_name": tool_name if node_type == "TOOL_CALL" else None,
        "tool_input": ti,
        "tool_output": to,
        "token_cost": token_cost,
        "latency_ms": latency_ms,
    }


def _format_content(inp: Any, out: Any) -> str:
    parts: list[str] = []
    if inp not in (None, "", {}):
        parts.append(f"in: {_short_json(inp)}")
    if out not in (None, "", {}):
        parts.append(f"out: {_short_json(out)}")
    return "\n".join(parts) if parts else ""


def _short_json(val: Any, max_len: int = 4000) -> str:
    try:
        s = val if isinstance(val, str) else json.dumps(val, default=str)
    except (TypeError, ValueError):
        s = str(val)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def _latency_ms(obs: dict[str, Any]) -> int:
    lat = obs.get("latency")
    if isinstance(lat, (int, float)) and lat > 0:
        return int(float(lat) * 1000)
    start_ms = _parse_iso_ms(obs.get("startTime"))
    end_ms = _parse_iso_ms(obs.get("endTime"))
    if start_ms is not None and end_ms is not None and end_ms >= start_ms:
        return end_ms - start_ms
    return 0


def _obs_cost(obs: dict[str, Any]) -> float:
    for key in ("calculatedTotalCost", "totalPrice"):
        v = obs.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    cd = obs.get("costDetails")
    if isinstance(cd, dict):
        t = cd.get("total")
        if isinstance(t, (int, float)):
            return float(t)
    return 0.0


def _tool_io(
    obs: dict[str, Any], node_type: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if node_type != "TOOL_CALL":
        return None, None
    inp, out = obs.get("input"), obs.get("output")
    return _as_obj(inp), _as_obj(out)


def _as_obj(val: Any) -> dict[str, Any] | None:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    return {"value": val}
