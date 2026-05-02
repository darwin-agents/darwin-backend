"""
/gateway — LLM Rate Limit Router (prevents Tragedy of the Commons).
"""

from __future__ import annotations

import asyncio
import time
from itertools import cycle

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentAgent, SettingsDep

router = APIRouter(prefix="/gateway", tags=["gateway"])


class InferenceRequest(BaseModel):
    model: str
    messages: list[dict]
    max_tokens: int = 1000
    temperature: float = 0.7
    stream: bool = False


@router.post("/inference")
async def proxy_inference(
    body: InferenceRequest,
    agent: CurrentAgent,
    settings: SettingsDep,
) -> dict:
    """
    Route LLM call through the platform key pool.
    Uses round-robin across available keys, tracking usage in Redis.
    """
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    if "claude" in body.model.lower():
        keys = settings.get_anthropic_keys()
        return await _call_anthropic(body, keys, redis)
    else:
        keys = settings.get_openai_keys()
        return await _call_openai(body, keys, redis)


async def _call_anthropic(body: InferenceRequest, keys: list[str], redis) -> dict:  # type: ignore[type-ignore]
    if not keys:
        raise HTTPException(status_code=503, detail={"error": "no_api_keys_configured"})

    # Pick the key with fewest in-flight requests
    key = await _pick_key(keys, redis, "anthropic")
    counter_key = f"gateway:rpm:anthropic:{key[-8:]}"
    await redis.incr(counter_key)
    await redis.expire(counter_key, 60)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": body.model,
                    "max_tokens": body.max_tokens,
                    "messages": body.messages,
                },
            )
            r.raise_for_status()
            data = r.json()
    finally:
        await redis.decr(counter_key)

    latency_ms = int((time.monotonic() - start) * 1000)
    content = data["content"][0]["text"] if data.get("content") else ""
    usage = data.get("usage", {})

    return {
        "content": content,
        "model": body.model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "latency_ms": latency_ms,
    }


async def _call_openai(body: InferenceRequest, keys: list[str], redis) -> dict:  # type: ignore[type-ignore]
    if not keys:
        raise HTTPException(status_code=503, detail={"error": "no_api_keys_configured"})

    key = await _pick_key(keys, redis, "openai")
    counter_key = f"gateway:rpm:openai:{key[-8:]}"
    await redis.incr(counter_key)
    await redis.expire(counter_key, 60)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": body.model,
                    "messages": body.messages,
                    "max_tokens": body.max_tokens,
                    "temperature": body.temperature,
                },
            )
            r.raise_for_status()
            data = r.json()
    finally:
        await redis.decr(counter_key)

    latency_ms = int((time.monotonic() - start) * 1000)
    content = data["choices"][0]["message"]["content"] if data.get("choices") else ""
    usage = data.get("usage", {})

    return {
        "content": content,
        "model": body.model,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "latency_ms": latency_ms,
    }


async def _pick_key(keys: list[str], redis, provider: str) -> str:
    """Pick the key with the lowest current RPM counter."""
    best_key = keys[0]
    best_count = float("inf")
    for k in keys:
        count = await redis.get(f"gateway:rpm:{provider}:{k[-8:]}")
        current = int(count or 0)
        if current < best_count:
            best_count = current
            best_key = k
    return best_key


@router.get("/limits")
async def get_limits(agent: CurrentAgent, settings: SettingsDep) -> dict:
    """Real-time API weather: remaining capacity per provider."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    providers = []
    for provider, keys in [("anthropic", settings.get_anthropic_keys()), ("openai", settings.get_openai_keys())]:
        total_rpm = 0
        for k in keys:
            count = await redis.get(f"gateway:rpm:{provider}:{k[-8:]}")
            total_rpm += int(count or 0)

        limit = 5000  # default RPM ceiling; TODO: read actual limits per key
        remaining = max(0, limit - total_rpm)
        congestion = "low" if total_rpm < limit * 0.5 else "medium" if total_rpm < limit * 0.8 else "high"
        providers.append({"provider": provider, "remaining_rpm": remaining, "congestion": congestion})

    action = "proceed"
    if any(p["congestion"] == "high" for p in providers):
        action = "hibernate_30m"
    elif any(p["congestion"] == "medium" for p in providers):
        action = "throttle"

    return {"providers": providers, "recommended_action": action}
