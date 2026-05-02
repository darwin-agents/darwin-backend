"""
RedisService — helper for job queues and telemetry time-series.

Uses redis.asyncio for non-blocking I/O consistent with FastAPI's event loop.
"""

from __future__ import annotations

import json
from datetime import timedelta

import redis.asyncio as aioredis

from app.config import Settings

# Key prefixes
_QUEUE_PREFIX = "queue:"
_TELEMETRY_PREFIX = "telemetry:ts:"
_SCHEDULE_PREFIX = "schedule:wake:"


class RedisService:
    def __init__(self, client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._r = client

    # ── Job Queues ────────────────────────────────────────────────────────────

    async def enqueue(self, queue: str, payload: dict) -> int:
        """Push a JSON payload onto a Redis list queue. Returns queue depth."""
        return await self._r.lpush(f"{_QUEUE_PREFIX}{queue}", json.dumps(payload))

    async def dequeue(self, queue: str, timeout: int = 0) -> dict | None:
        """Blocking pop from queue. Returns None on timeout."""
        result = await self._r.brpop(f"{_QUEUE_PREFIX}{queue}", timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    # ── Telemetry Time-Series ─────────────────────────────────────────────────

    async def push_metabolic_sample(self, agent_id: str, sample: dict) -> None:
        key = f"{_TELEMETRY_PREFIX}{agent_id}"
        await self._r.lpush(key, json.dumps(sample))
        await self._r.ltrim(key, 0, 999)   # keep last 1000 samples
        await self._r.expire(key, int(timedelta(hours=48).total_seconds()))

    async def get_recent_samples(self, agent_id: str, count: int = 60) -> list[dict]:
        key = f"{_TELEMETRY_PREFIX}{agent_id}"
        raw = await self._r.lrange(key, 0, count - 1)
        return [json.loads(r) for r in raw]

    # ── Wake-Up Scheduling ────────────────────────────────────────────────────

    async def schedule_wake(self, agent_id: str, wake_at_unix: float) -> None:
        """Register a future wake-up timestamp in a sorted set."""
        await self._r.zadd("schedule:wake_queue", {agent_id: wake_at_unix})

    async def get_due_wakeups(self, now_unix: float) -> list[str]:
        """Return agent IDs whose wake-up time has elapsed."""
        return await self._r.zrangebyscore("schedule:wake_queue", 0, now_unix)

    async def clear_wake(self, agent_id: str) -> None:
        await self._r.zrem("schedule:wake_queue", agent_id)
