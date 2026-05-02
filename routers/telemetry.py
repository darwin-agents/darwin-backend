"""
/telemetry — Diagnostics, metabolic streaming, and circuit breaker.
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status

from deps import CurrentAgent, CurrentUser, PoolDep, SettingsDep
from schemas.telemetry import (
    CanvasEventRequest,
    CircuitBreakerTripRequest,
    EntityPushRequest,
    TelemetryStreamRequest,
    VALID_EVENT_TYPES,
)
from services.canvas_service import CanvasService

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

    from workers.telemetry_worker import persist_telemetry_batch
    persist_telemetry_batch.delay(
        body.darwin_id,
        body.session_id,
        body.metrics.model_dump(),
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

    canvas = CanvasService(pool)
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
