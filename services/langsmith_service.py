"""
LangSmithService — fetch structured thought logs (traces) for the Mental Replay panel.

Queries LangSmith (or Arize Phoenix) for the agent's cognitive trace
for a given session, then caches the result in Redis for 5 minutes.
"""

from __future__ import annotations

import json

import httpx
import redis.asyncio as aioredis

from config import Settings

_CACHE_TTL = 300  # 5 minutes


class LangSmithService:
    def __init__(self, settings: Settings, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._api_key = settings.langsmith_api_key.get_secret_value()
        self._project = settings.langchain_project
        self._redis = redis
        self._base = "https://api.smith.langchain.com"

    async def get_trace(
        self,
        session_id: str,
        neuron_id: str | None = None,
    ) -> list[dict]:
        """
        Fetch and cache the cognitive trace for a session.
        Returns a list of trace nodes sorted by timestamp.
        """
        cache_key = f"trace:{session_id}:{neuron_id or 'main'}"
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)

        if not self._api_key:
            return []

        try:
            runs = await self._fetch_runs(session_id, neuron_id)
        except Exception:
            return []

        await self._redis.set(cache_key, json.dumps(runs), ex=_CACHE_TTL)
        return runs

    async def _fetch_runs(
        self, session_id: str, neuron_id: str | None
    ) -> list[dict]:
        """Query LangSmith runs API for this session."""
        params: dict = {
            "project_name": self._project,
            "filter": f'has(tags, "session:{session_id}")',
        }
        if neuron_id:
            params["filter"] += f' AND has(tags, "neuron:{neuron_id}")'

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/runs",
                params=params,
                headers={"x-api-key": self._api_key},
            )
            r.raise_for_status()
            data = r.json()

        return [
            {
                "node_id": run["id"],
                "parent_node_id": run.get("parent_run_id"),
                "type": _map_run_type(run.get("run_type", "")),
                "timestamp_ms": int(run["start_time"] * 1000) if run.get("start_time") else 0,
                "content": run.get("outputs", {}).get("output", ""),
                "tool_name": run.get("name") if run.get("run_type") == "tool" else None,
                "tool_input": run.get("inputs"),
                "tool_output": run.get("outputs"),
                "token_cost": _calc_cost(run),
                "latency_ms": _calc_latency(run),
            }
            for run in data.get("runs", [])
        ]


def _map_run_type(run_type: str) -> str:
    mapping = {"llm": "LLM_INFERENCE", "tool": "TOOL_CALL", "chain": "THOUGHT"}
    return mapping.get(run_type, "THOUGHT")


def _calc_cost(run: dict) -> float:
    usage = run.get("total_cost")
    return float(usage) if usage else 0.0


def _calc_latency(run: dict) -> int:
    start = run.get("start_time")
    end = run.get("end_time")
    if start and end:
        return int((end - start) * 1000)
    return 0
