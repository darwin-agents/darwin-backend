"""
/webhooks — External Event Switchboard.

All routes respond with 200 OK within 10 ms.
Actual processing runs in asyncio background tasks.
"""

from __future__ import annotations

import asyncio
import json

import stripe
from fastapi import APIRouter, Header, HTTPException, Request, status

from config import Settings
from deps import SettingsDep
from db.client import get_pool

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    settings: SettingsDep,
    stripe_signature: str = Header(..., alias="stripe-signature"),
) -> dict:
    """Verify Stripe signature → enqueue payload → return 200 in <10 ms."""
    payload = await request.body()
    try:
        stripe.Webhook.construct_event(
            payload,
            stripe_signature,
            settings.stripe_webhook_secret.get_secret_value(),
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_signature"},
        )

    from workers.webhook_worker import _process_stripe
    asyncio.create_task(_process_stripe(payload.decode()))
    return {"received": True}


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_webhook(
    request: Request,
    settings: SettingsDep,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict:
    """Verify Telegram token → enqueue → return 200."""
    secret = settings.telegram_bot_token.get_secret_value()
    # Telegram webhooks use the secret token header for verification
    if x_telegram_bot_api_secret_token and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail={"error": "invalid_signature"})

    raw = await request.body()
    from workers.webhook_worker import _process_telegram
    asyncio.create_task(_process_telegram(raw.decode()))
    return {"received": True}


@router.post("/email", status_code=status.HTTP_200_OK)
async def email_webhook(request: Request) -> dict:
    """Receive inbound email (SendGrid Inbound Parse) → enqueue."""
    raw = await request.body()
    from workers.webhook_worker import _process_email
    asyncio.create_task(_process_email(raw.decode()))
    return {"received": True}


@router.post("/modal", status_code=status.HTTP_200_OK)
async def modal_webhook(
    request: Request,
    settings: SettingsDep,
    x_modal_signature: str | None = Header(None),
) -> dict:
    """
    Modal container lifecycle events (started, exited_unexpectedly).
    On unexpected exit → transition agent to HIBERNATING, schedule auto-resume.
    """
    import hashlib
    import hmac

    raw = await request.body()
    modal_secret = settings.modal_webhook_secret.get_secret_value()
    if modal_secret and x_modal_signature:
        expected = hmac.new(modal_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_modal_signature):
            raise HTTPException(status_code=403, detail={"error": "invalid_signature"})

    payload = json.loads(raw)
    event_type = payload.get("type")
    darwin_id = payload.get("darwin_id")

    if event_type == "container.exited_unexpectedly" and darwin_id:
        from workers.telemetry_worker import _check_health
        asyncio.create_task(_check_health())

    return {"received": True}
