"""
Telemetry workers — persist metrics batches and check container health.
"""

from __future__ import annotations

import asyncio
import logging
import time

from workers.app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(queue="telemetry")
def persist_telemetry_batch(darwin_id: str, session_id: str, metrics: dict) -> None:
    asyncio.get_event_loop().run_until_complete(
        _persist_batch(darwin_id, session_id, metrics)
    )


async def _persist_batch(darwin_id: str, session_id: str, metrics: dict) -> None:
    import json

    import redis.asyncio as aioredis

    from config import get_settings
    from db.client import get_pool

    settings = get_settings()
    pool = await get_pool()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    ts = int(time.time())
    sample = {**metrics, "darwin_id": darwin_id, "session_id": session_id, "ts": ts}
    key = f"telemetry:ts:{darwin_id}"
    await redis.lpush(key, json.dumps(sample))
    await redis.ltrim(key, 0, 999)
    await redis.expire(key, 172800)  # 48h

    # Update agent totals in DB
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE darwins SET
              total_tokens_burned = total_tokens_burned + $2,
              total_compute_cost  = total_compute_cost  + $3,
              last_active_at      = now()
            WHERE id = $1
            """,
            darwin_id,
            metrics.get("tokens_burned_last_30s", 0),
            metrics.get("compute_cost_last_30s", 0),
        )

    # Check circuit breaker
    await _check_circuit_breaker(darwin_id, metrics, settings, pool, redis)


async def _check_circuit_breaker(darwin_id: str, metrics: dict, settings, pool, redis) -> None:  # type: ignore[type-ignore]
    import json

    from services.telegram_service import TelegramService

    key = f"telemetry:ts:{darwin_id}"
    samples = await redis.lrange(key, 0, 9)  # last 10 samples (5 minutes)
    if len(samples) < 3:
        return

    parsed = [json.loads(s) for s in samples]

    # Historical average burn rate (tokens/min normalised)
    avg_tokens = sum(p.get("tokens_burned_last_30s", 0) for p in parsed) / len(parsed)
    current = metrics.get("tokens_burned_last_30s", 0)

    failed = metrics.get("failed_http_requests_last_30s", 0)

    if avg_tokens > 0 and current > avg_tokens * settings.cb_burn_multiplier:
        logger.warning("Circuit breaker: metabolic runaway on %s", darwin_id)
        await _trip(darwin_id, "metabolic_runaway", pool, settings)
    elif failed >= settings.cb_failed_http_threshold:
        logger.warning("Circuit breaker: HTTP failure storm on %s", darwin_id)
        await _trip(darwin_id, "http_failure_storm", pool, settings)


async def _trip(darwin_id: str, trigger: str, pool, settings) -> None:  # type: ignore[type-ignore]
    import redis.asyncio as aioredis

    from services.canvas_service import CanvasService
    from services.modal_service import ModalService
    from services.telegram_service import TelegramService

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    canvas = CanvasService(pool)
    modal = ModalService(settings, redis)
    telegram = TelegramService(settings)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT name, state FROM darwins WHERE id = $1", darwin_id)
        if not row or row["state"] != "ACTIVE":
            return
        await conn.execute(
            "UPDATE darwins SET state = 'TERMINATED', terminated_at = now() WHERE id = $1",
            darwin_id,
        )

    await modal.kill_container(darwin_id)

    from uuid import uuid4
    session_id = str(uuid4())
    await canvas.push_event(
        darwin_id=darwin_id,
        session_id=session_id,
        event_type="CIRCUIT_BREAKER_TRIPPED",
        payload={"trigger": trigger},
    )
    await telegram.send_alert_by_darwin_id(
        pool,
        darwin_id,
        f"🚨 <b>Emergency Circuit Breaker</b> tripped on Agent <b>{row['name']}</b>\n"
        f"Trigger: <code>{trigger}</code> — container incinerated, VCC frozen.",
    )


@celery_app.task(queue="telemetry")
def check_container_health() -> None:
    """
    Celery Beat task — runs every 60s.
    Detects ACTIVE agents whose containers have silently died
    and transitions them to HIBERNATING with an auto-resume.
    """
    asyncio.get_event_loop().run_until_complete(_check_health())


async def _check_health() -> None:
    import redis.asyncio as aioredis

    from config import get_settings
    from db.client import get_pool
    from services.modal_service import ModalService
    from services.telegram_service import TelegramService

    settings = get_settings()
    pool = await get_pool()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)
    telegram = TelegramService(settings)

    async with pool.acquire() as conn:
        active_agents = await conn.fetch(
            "SELECT id, name FROM darwins WHERE state = 'ACTIVE'"
        )

    for agent in active_agents:
        darwin_id = str(agent["id"])
        status = await modal.get_container_status(darwin_id)
        if status == "stopped":
            logger.warning("Container for %s stopped unexpectedly", darwin_id)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE darwins SET state = 'HIBERNATING' WHERE id = $1",
                    darwin_id,
                )
                await conn.execute(
                    """
                    INSERT INTO hibernate_schedule
                      (darwin_id, trigger_type, trigger_at, wake_prompt)
                    VALUES ($1, 'CRON', now() + interval '5 minutes',
                      'Your container crashed unexpectedly. Resume from last known state.')
                    """,
                    darwin_id,
                )

            await telegram.send_alert_by_darwin_id(
                pool,
                darwin_id,
                f"⚠️ Agent <b>{agent['name']}</b> container crashed unexpectedly. "
                "Will auto-resume in 5 minutes.",
            )
