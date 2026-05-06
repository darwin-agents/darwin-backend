"""
/telemetry — Diagnostics, metabolic streaming, and circuit breaker.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

import asyncpg
from asyncpg.exceptions import UniqueViolationError
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status

from deps import CurrentAgent, CurrentUser, PoolDep, SettingsDep
from schemas.telemetry import (
    CanvasEventRequest,
    CircuitBreakerTripRequest,
    EntityPushRequest,
    LLMCallRequest,
    TelemetryStreamRequest,
    VALID_EVENT_TYPES,
)
from schemas.finance import LedgerUpdateRequest
from services.canvas_service import CanvasService
from services.ledger_service import InsufficientFundsError, LedgerService

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/stream", status_code=status.HTTP_202_ACCEPTED)
async def stream_metrics(
    body: TelemetryStreamRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Agent pushes metabolic metrics every 30 seconds.
    Enqueued to Celery worker for persistence and circuit-breaker analysis.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    from workers.telemetry_worker import _persist_batch
    asyncio.create_task(
        _persist_batch(body.darwin_id, body.session_id, body.metrics.model_dump())
    )
    return {"queued": True}


@router.post("/stream-event", status_code=status.HTTP_201_CREATED)
async def stream_event(
    body: CanvasEventRequest,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """
    Agent pushes a single cognitive event for Infinite Canvas display.
    This is how every thought, tool call, screenshot, sub-agent spawn, etc.
    gets surfaced to the frontend in real time.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    if body.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=422, detail={"error": "invalid_event_type"})

    if body.event_type == "THOUGHT" and not body.payload.get("model"):
        raise HTTPException(status_code=422, detail={"error": "THOUGHT event must include payload.model"})

    canvas = CanvasService(pool)
    try:
        event_id = await canvas.push_event(
            darwin_id=body.darwin_id,
            session_id=body.session_id,
            event_type=body.event_type,
            payload=body.payload,
            neuron_id=body.neuron_id,
            parent_event_id=body.parent_event_id,
            token_cost=body.token_cost,
            fiat_cost=body.fiat_cost,
        )
    except asyncpg.PostgresError as exc:
        raise HTTPException(status_code=500, detail={"error": "db_error", "detail": str(exc)}) from exc
    return {"event_id": event_id}


@router.post("/entity", status_code=status.HTTP_201_CREATED)
async def push_entity(
    body: EntityPushRequest,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """
    Agent pushes a new/updated SQLite entity.
    Upserted into hermes_entities; canvas event fires for the frontend Entities panel.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    import json as _json

    async with pool.acquire() as conn:
        if body.operation == "DELETE":
            await conn.execute(
                "UPDATE hermes_entities SET is_deleted = TRUE, updated_at = now() "
                "WHERE darwin_id = $1 AND entity_type = $2 AND entity_id = $3",
                body.darwin_id, body.entity_type, body.entity_id,
            )
        else:
            await conn.execute(
                """
                INSERT INTO hermes_entities (darwin_id, session_id, entity_type, entity_id, data)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (darwin_id, entity_type, entity_id)
                DO UPDATE SET data = EXCLUDED.data, updated_at = now(), is_deleted = FALSE
                """,
                body.darwin_id, body.session_id, body.entity_type, body.entity_id,
                _json.dumps(body.data),
            )

    canvas = CanvasService(pool)
    event_type = "ENTITY_CREATED" if body.operation == "CREATE" else "ENTITY_UPDATED"
    asyncio.create_task(canvas.push_event(
        darwin_id=body.darwin_id,
        session_id=body.session_id,
        event_type=event_type,
        payload={
            "entity_type": body.entity_type,
            "entity_id": body.entity_id,
            "data": body.data,
            "operation": body.operation,
        },
    ))
    return {"upserted": True}


@router.post("/trip", status_code=status.HTTP_200_OK)
async def trip_circuit_breaker(
    body: CircuitBreakerTripRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Emergency circuit breaker: log → Telegram alert → terminate agent.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    from workers.telemetry_worker import _trip
    asyncio.create_task(_trip(body.darwin_id, body.trigger, pool, settings))
    return {"tripped": True}


@router.post("/llm-call", status_code=status.HTTP_201_CREATED)
async def record_llm_call(
    body: LLMCallRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Agent records an LLM API call for cost tracking.
    Fired by HooksManager.post_llm_call() after every inference.

    Debit path: idempotent ledger row keyed by llm:<request_id>, daily rollups + optional session cap (Redis).
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    rid = (body.request_id or "").strip() or str(uuid.uuid4())

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM llm_calls WHERE darwin_id = $1::text AND request_id = $2",
            body.darwin_id,
            rid,
        )
        if exists:
            return {"recorded": True, "request_id": rid, "deduplicated": True}

    session_key = f"telemetry:session_llm_usd:{body.darwin_id}:{body.session_id}"
    cap = float(settings.session_llm_spend_cap_usd or 0.0)
    if cap > 0:
        cur_raw = await redis.get(session_key)
        current_usd = float(cur_raw or 0.0)
        if current_usd + float(body.cost_usd) > cap + 1e-9:
            raise HTTPException(
                status_code=429,
                detail={"error": "session_llm_spend_cap_exceeded"},
            )

    ledger_ext = f"llm:{rid}"
    ledger = LedgerService(pool, settings)
    amount = Decimal(str(-abs(body.cost_usd))) if body.cost_usd > 0 else Decimal("0")
    if amount < 0:
        try:
            await ledger.apply_transaction(
                LedgerUpdateRequest(
                    darwin_id=body.darwin_id,
                    session_id=body.session_id,
                    amount=amount,
                    type="LLM_USAGE_DEBIT",
                    description=f"OpenRouter inference {body.model}",
                    external_ref=ledger_ext,
                )
            )
        except UniqueViolationError:
            pass
        except InsufficientFundsError as exc:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "insufficient_ledger_balance",
                    "balance": float(exc.balance),
                    "requested": float(exc.requested),
                },
            ) from exc

    in_mod_json = json.dumps(body.input_modalities or [])
    out_mod_json = json.dumps(body.output_modalities or [])
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls
                    (darwin_id, session_id, model, provider, input_tokens, output_tokens,
                     cache_read_tokens, reasoning_tokens, cost_usd, duration_ms, turn_id,
                     request_id, input_modalities, output_modalities,
                     image_output_count, audio_output_seconds, video_output_seconds, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,
                        $15,$16,$17,now())
                """,
                body.darwin_id,
                body.session_id,
                body.model,
                body.provider,
                body.input_tokens,
                body.output_tokens,
                body.cache_read_tokens,
                body.reasoning_tokens,
                body.cost_usd,
                body.duration_ms,
                body.turn_id,
                rid,
                in_mod_json,
                out_mod_json,
                body.image_output_count,
                body.audio_output_seconds,
                body.video_output_seconds,
            )
    except UniqueViolationError:
        return {"recorded": True, "request_id": rid, "deduplicated": True}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO darwin_spend_daily (darwin_id, day, llm_cost_usd, token_count, call_count)
            VALUES ($1::uuid, (now() AT TIME ZONE 'utc')::date, $2::numeric, $3::bigint, 1)
            ON CONFLICT (darwin_id, day) DO UPDATE SET
              llm_cost_usd = darwin_spend_daily.llm_cost_usd + EXCLUDED.llm_cost_usd,
              token_count = darwin_spend_daily.token_count + EXCLUDED.token_count,
              call_count = darwin_spend_daily.call_count + 1
            """,
            body.darwin_id,
            str(body.cost_usd),
            body.input_tokens + body.output_tokens,
        )

    if cap > 0:
        await redis.incrbyfloat(session_key, float(body.cost_usd))

    # Push LLM_INFERENCE canvas event for the Mental Replay overlay
    canvas = CanvasService(pool)
    asyncio.create_task(
        canvas.push_event(
            darwin_id=body.darwin_id,
            session_id=body.session_id,
            event_type="LLM_INFERENCE",
            payload={
                "model": body.model,
                "provider": body.provider,
                "input_tokens": body.input_tokens,
                "output_tokens": body.output_tokens,
                "cache_read_tokens": body.cache_read_tokens,
                "reasoning_tokens": body.reasoning_tokens,
                "cost_usd": body.cost_usd,
                "duration_ms": body.duration_ms,
                "turn_id": body.turn_id,
                "request_id": rid,
                "modalities_in": body.input_modalities,
                "modalities_out": body.output_modalities,
            },
            token_cost=float(body.input_tokens + body.output_tokens),
            fiat_cost=body.cost_usd,
        )
    )
    return {"recorded": True, "request_id": rid}


@router.get("/{darwin_id}/history")
async def get_history(
    darwin_id: str,
    user: CurrentUser,
    settings: SettingsDep,
    hours: int = 24,
) -> list[dict]:
    """Historical burn rate chart data for Token ROI dashboard."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    key = f"telemetry:ts:{darwin_id}"
    raw = await redis.lrange(key, 0, 999)
    samples = [json.loads(r) for r in raw]

    # Bucket by hour
    from collections import defaultdict
    buckets: dict[int, dict] = defaultdict(lambda: {"tokens_burned": 0, "llm_cost": 0.0, "compute_cost": 0.0, "revenue": 0.0})
    for s in samples:
        ts = s.get("ts", 0)
        bucket_hour = (ts // 3600) * 3600 * 1000  # ms
        b = buckets[bucket_hour]
        b["tokens_burned"] += s.get("tokens_burned_last_30s", 0)
        b["llm_cost"] += s.get("llm_cost_last_30s", 0.0)
        b["compute_cost"] += s.get("compute_cost_last_30s", 0.0)

    return [{"timestamp": k, **v} for k, v in sorted(buckets.items())]
