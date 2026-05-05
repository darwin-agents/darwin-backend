"""
/gateway — LLM proxy through OpenRouter (single API surface).

Multiplex keys for rate-limit spread; optionally require positive Darwin ledger balance.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentAgent, PoolDep, SettingsDep
from services.openrouter import chat_completions_json, normalize_chat_usage, openrouter_headers

router = APIRouter(prefix="/gateway", tags=["gateway"])


class InferenceRequest(BaseModel):
    """OpenRouter OpenAI-compatible chat completion request surface."""

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int = 1000
    temperature: float = 0.7
    stream: bool = False
    # Forward extra OpenRouter knobs ( modalities, reasoning, routing, … )
    extra: dict[str, Any] | None = None


@router.post("/inference")
async def proxy_inference(
    body: InferenceRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Route chat completion via OpenRouter with round-robin key selection.
    """
    keys = settings.get_openrouter_keys()
    if not getattr(settings, "use_openrouter", True):
        raise HTTPException(status_code=503, detail={"error": "openrouter_disabled"})

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    darwin_id = agent["darwin_id"]

    if settings.gateway_min_ledger_balance is not None and settings.gateway_min_ledger_balance >= 0:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ledger_balance FROM darwins WHERE id = $1::uuid",
                darwin_id,
            )
        balance = Decimal(str(row["ledger_balance"])) if row else Decimal("0")
        if balance <= Decimal(str(settings.gateway_min_ledger_balance)):
            raise HTTPException(
                status_code=402,
                detail={"error": "insufficient_ledger_balance", "balance": float(balance)},
            )

    return await _call_openrouter(body, keys, redis, settings)


async def _call_openrouter(
    body: InferenceRequest,
    keys: list[str],
    redis: aioredis.Redis,
    settings,
) -> dict:
    from config import Settings

    _: Settings = settings  # noqa: ARG001 typing hint

    if not keys:
        raise HTTPException(status_code=503, detail={"error": "no_api_keys_configured"})

    key = await _pick_key(keys, redis)
    counter_key = f"gateway:rpm:openrouter:{key[-8:]}"
    await redis.incr(counter_key)
    await redis.expire(counter_key, 60)

    hdrs = openrouter_headers(
        key,
        settings.openrouter_http_referer,
        settings.openrouter_app_title,
    )
    start = time.monotonic()

    chat_body: dict[str, Any] = {
        "model": body.model,
        "messages": body.messages,
        "max_tokens": body.max_tokens,
        "temperature": body.temperature,
        "stream": body.stream,
    }
    if body.extra:
        chat_body.update(body.extra)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                headers=hdrs,
                json=chat_body,
            )
            r.raise_for_status()
            data = r.json()
    finally:
        await redis.decr(counter_key)

    latency_ms = int((time.monotonic() - start) * 1000)
    choices = data.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    content = msg.get("content")

    usage = normalize_chat_usage(data)

    payload: dict[str, Any] = {
        "content": content if isinstance(content, str) else str(content),
        "model": data.get("model", body.model),
        "role": msg.get("role"),
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "reasoning_tokens": usage["reasoning_tokens"],
        "cache_read_input_tokens": usage["cache_read_input_tokens"],
        "raw": data,
        "latency_ms": latency_ms,
    }
    return payload


async def _pick_key(keys: list[str], redis: aioredis.Redis) -> str:
    best_key = keys[0]
    best_count = float("inf")
    for k in keys:
        count = await redis.get(f"gateway:rpm:openrouter:{k[-8:]}")
        current = int(count or 0)
        if current < best_count:
            best_count = current
            best_key = k
    return best_key


@router.get("/limits")
async def get_limits(_agent: CurrentAgent, settings: SettingsDep) -> dict:
    """Real-time congestion hint for pooled OpenRouter keys."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys = settings.get_openrouter_keys()

    limit = max(6000 * max(1, len(keys)), 1)
    total_rpm = 0
    for k in keys:
        count = await redis.get(f"gateway:rpm:openrouter:{k[-8:]}")
        total_rpm += int(count or 0)

    remaining = max(0, limit - total_rpm)
    congestion = "low" if total_rpm < limit * 0.5 else "medium" if total_rpm < limit * 0.8 else "high"

    providers = [{"provider": "openrouter", "remaining_rpm": remaining, "congestion": congestion}]
    action = "proceed"
    if congestion == "high":
        action = "hibernate_30m"
    elif congestion == "medium":
        action = "throttle"

    return {"providers": providers, "recommended_action": action}
