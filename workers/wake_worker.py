"""
Wake workers — execute agent wake-up sequences and cron scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from workers.app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(queue="wake")
def execute_agent_wake(darwin_id: str, wake_prompt: str, trigger_source: str) -> None:
    """
    Resurrect a hibernating agent.
    1. Verify state is HIBERNATING.
    2. Mint a fresh agent JWT.
    3. Spawn Modal container with the wake prompt.
    4. Update state to ACTIVE.
    5. Emit AGENT_WOKE canvas event.
    6. Send Telegram notification.
    """
    asyncio.get_event_loop().run_until_complete(
        _execute_wake(darwin_id, wake_prompt, trigger_source)
    )


async def _execute_wake(darwin_id: str, wake_prompt: str, trigger_source: str) -> None:
    import redis.asyncio as aioredis

    from config import get_settings
    from db.client import get_pool
    from deps import mint_agent_jwt
    from services.canvas_service import CanvasService
    from services.modal_service import ModalService
    from services.telegram_service import TelegramService

    settings = get_settings()
    pool = await get_pool()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)
    canvas = CanvasService(pool)
    telegram = TelegramService(settings)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, name, ledger_balance, spawned_at FROM darwins WHERE id = $1 FOR UPDATE",
            darwin_id,
        )
        if not row or row["state"] != "HIBERNATING":
            logger.warning("wake attempt on non-hibernating agent %s (state=%s)", darwin_id, row["state"] if row else "not found")
            return

        session_id = str(uuid4())
        agent_jwt = mint_agent_jwt(darwin_id, session_id, settings)
        image_tag = await modal.get_image_tag(darwin_id)

        modal_call_id = await modal.spawn_container(
            darwin_id=darwin_id,
            session_id=session_id,
            wake_prompt=wake_prompt,
            agent_jwt=agent_jwt,
            image_tag=image_tag,
        )

        spawned_at = row["spawned_at"]
        hibernated_hours = 0.0
        if spawned_at:
            hibernated_hours = round((time.time() - spawned_at.timestamp()) / 3600, 2)

        await conn.execute(
            """
            UPDATE darwins SET state = 'ACTIVE', last_active_at = now(),
              modal_call_id = $2
            WHERE id = $1
            """,
            darwin_id, modal_call_id,
        )
        # Mark hibernate schedule row as fired
        await conn.execute(
            "UPDATE hibernate_schedule SET fired_at = now() WHERE darwin_id = $1 AND fired_at IS NULL",
            darwin_id,
        )

    await canvas.push_lifecycle(
        darwin_id=darwin_id,
        session_id=session_id,
        event_type="AGENT_WOKE",
        payload={
            "wake_reason": wake_prompt[:200],
            "session_id": session_id,
            "balance_at_wake": float(row["ledger_balance"]),
            "hibernated_for_hours": hibernated_hours,
        },
    )

    await telegram.send_alert_by_darwin_id(
        pool, darwin_id, f"🟢 Agent <b>{row['name']}</b> is now active.\nReason: {trigger_source}"
    )


@celery_app.task(queue="wake")
def check_due_wakeups() -> None:
    """Celery Beat task — fires every 30s to process CRON wake-up triggers."""
    asyncio.get_event_loop().run_until_complete(_check_due_wakeups())


async def _check_due_wakeups() -> None:
    from db.client import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT hs.darwin_id, hs.wake_prompt
            FROM hibernate_schedule hs
            JOIN darwins d ON d.id = hs.darwin_id
            WHERE hs.trigger_type = 'CRON'
              AND hs.trigger_at <= now()
              AND hs.fired_at IS NULL
              AND d.state = 'HIBERNATING'
            """,
        )

    for row in rows:
        execute_agent_wake.delay(
            str(row["darwin_id"]), row["wake_prompt"], "cron_timer"
        )
