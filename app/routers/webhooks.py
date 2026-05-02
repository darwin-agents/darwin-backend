"""
/webhooks — External Webhook Catchers

Massive, highly concurrent switchboard for inbound Web2 signals.
Pattern: acknowledge fast (<10 ms) → enqueue to Redis → background workers handle logic.
"""

import hashlib
import hmac

import stripe
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.dependencies import RedisDep, SettingsDep
from app.core.exceptions import WebhookVerificationFailed
from app.schemas.webhooks import EmailWebhookPayload, TelegramUpdate, WebhookAck

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_STRIPE_JOB_QUEUE = "queue:webhooks:stripe"
_TELEGRAM_JOB_QUEUE = "queue:webhooks:telegram"
_EMAIL_JOB_QUEUE = "queue:webhooks:email"


@router.post("/stripe", response_model=WebhookAck, status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    redis: RedisDep,
    settings: SettingsDep,
    stripe_signature: str = Header(..., alias="stripe-signature"),
) -> WebhookAck:
    """
    Receive Stripe events (checkout.session.completed, etc.).

    Signature verified before processing. Raw payload is immediately dropped
    into Redis queue; background worker wakes the relevant agent.
    """
    payload = await request.body()
    try:
        stripe.Webhook.construct_event(
            payload,
            stripe_signature,
            settings.stripe_webhook_secret.get_secret_value(),
        )
    except stripe.SignatureVerificationError:
        raise WebhookVerificationFailed()

    job_id = await redis.lpush(_STRIPE_JOB_QUEUE, payload.decode())
    return WebhookAck(queued_job_id=str(job_id))


@router.post("/telegram", response_model=WebhookAck, status_code=status.HTTP_200_OK)
async def telegram_webhook(
    update: TelegramUpdate,
    redis: RedisDep,
    settings: SettingsDep,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> WebhookAck:
    """
    Receive Telegram bot updates (human replies to ask_human, approval buttons, etc.).
    """
    secret = settings.telegram_webhook_secret.get_secret_value()
    if secret and x_telegram_bot_api_secret_token != secret:
        raise WebhookVerificationFailed()

    job_id = await redis.lpush(_TELEGRAM_JOB_QUEUE, update.model_dump_json())
    return WebhookAck(queued_job_id=str(job_id))


@router.post("/email", response_model=WebhookAck, status_code=status.HTTP_200_OK)
async def email_webhook(
    payload: EmailWebhookPayload,
    redis: RedisDep,
) -> WebhookAck:
    """
    Receive inbound email events from providers like Postmark or SendGrid.
    Routes the message to the agent that owns the target address.
    """
    job_id = await redis.lpush(_EMAIL_JOB_QUEUE, payload.model_dump_json())
    return WebhookAck(queued_job_id=str(job_id))
