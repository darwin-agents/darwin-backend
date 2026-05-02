"""
Webhook workers — process Stripe, Telegram, and email payloads from Redis queue.

Pattern: routers acknowledge in <10 ms → enqueue raw payload → worker processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from workers.app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="webhooks")
def process_stripe_webhook(self, raw_payload: str) -> None:
    """
    Process a verified Stripe webhook event.
    Identifies the agent from payment link metadata → credits ledger → wakes agent.
    """
    asyncio.get_event_loop().run_until_complete(_process_stripe(raw_payload))


async def _process_stripe(raw_payload: str) -> None:
    import stripe

    from config import get_settings
    from db.client import get_pool
    from services.canvas_service import CanvasService
    from services.ledger_service import LedgerService
    from schemas.finance import LedgerUpdateRequest
    from decimal import Decimal

    settings = get_settings()
    event = stripe.Event.construct_from(json.loads(raw_payload), settings.stripe_webhook_secret.get_secret_value())

    if event["type"] not in ("checkout.session.completed", "payment_intent.succeeded"):
        return

    obj = event["data"]["object"]
    darwin_id = (obj.get("metadata") or {}).get("darwin_id")
    if not darwin_id:
        logger.warning("Stripe webhook missing darwin_id in metadata")
        return

    amount = obj.get("amount_received") or obj.get("amount", 0)
    amount_usd = Decimal(str(amount)) / 100  # Stripe amounts in cents

    pool = await get_pool()
    ledger = LedgerService(pool, settings)
    session_id = str(uuid4())

    await ledger.apply_transaction(
        LedgerUpdateRequest(
            darwin_id=darwin_id,
            session_id=session_id,
            amount=amount_usd,
            type="REVENUE_CREDIT",
            description=f"Stripe payment — {event['type']}",
            external_ref=obj.get("payment_intent") or obj.get("id"),
        )
    )

    wake_prompt = (
        f"Webhook from Stripe received. Payment of ${amount_usd:.2f} captured. "
        "Resume operations."
    )
    from workers.wake_worker import execute_agent_wake
    execute_agent_wake.delay(darwin_id, wake_prompt, "stripe_webhook")


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="webhooks")
def process_telegram_webhook(self, raw_payload: str) -> None:
    asyncio.get_event_loop().run_until_complete(_process_telegram(raw_payload))


async def _process_telegram(raw_payload: str) -> None:
    from config import get_settings
    from db.client import get_pool

    settings = get_settings()
    pool = await get_pool()
    update = json.loads(raw_payload)

    # Extract human response — either a plain text message or a callback button
    text: str | None = None
    webhook_key: str | None = None

    if "message" in update:
        text = update["message"].get("text", "")
        # The agent registered a webhook_key matching this Telegram reply_to_message_id
        reply_to = (update["message"].get("reply_to_message") or {}).get("message_id")
        if reply_to:
            webhook_key = f"telegram_{reply_to}"
    elif "callback_query" in update:
        cq = update["callback_query"]
        text = cq.get("data", "")
        webhook_key = f"telegram_{cq['message']['message_id']}"

    if not text or not webhook_key:
        return

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT darwin_id, wake_prompt FROM hibernate_schedule WHERE webhook_key = $1 AND fired_at IS NULL",
            webhook_key,
        )

    if not row:
        return

    darwin_id = str(row["darwin_id"])
    wake_prompt = f"{row['wake_prompt']}\n\nHuman responded: '{text}'"

    from workers.wake_worker import execute_agent_wake
    execute_agent_wake.delay(darwin_id, wake_prompt, "telegram_webhook")


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="webhooks")
def process_email_webhook(self, raw_payload: str) -> None:
    asyncio.get_event_loop().run_until_complete(_process_email(raw_payload))


async def _process_email(raw_payload: str) -> None:
    from db.client import get_pool

    pool = await get_pool()
    payload = json.loads(raw_payload)
    darwin_id = payload.get("darwin_id")
    if not darwin_id:
        return

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT wake_prompt FROM hibernate_schedule WHERE darwin_id = $1 AND fired_at IS NULL ORDER BY created_at DESC LIMIT 1",
            darwin_id,
        )

    if not row:
        return

    subject = payload.get("subject", "")
    body = payload.get("body_text", "")[:2000]
    wake_prompt = f"{row['wake_prompt']}\n\nInbound email received.\nSubject: {subject}\nBody: {body}"

    from workers.wake_worker import execute_agent_wake
    execute_agent_wake.delay(darwin_id, wake_prompt, "email_webhook")
