"""
/notifications — Human-in-the-loop Telegram push and session link generation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from config import Settings
from db.client import get_pool
from deps import CurrentAgent, PoolDep, SettingsDep, decode_session_link_jwt, mint_session_link_jwt
from services.canvas_service import CanvasService
from services.telegram_service import TelegramService

router = APIRouter(prefix="/notifications", tags=["notifications"])


class SendNotificationRequest(BaseModel):
    darwin_id: str
    message: str
    screenshot_url: str | None = None
    action_type: str  # ASK_HUMAN | APPROVAL | ALERT
    options: list[str] | None = None  # inline button labels


class SessionLinkRequest(BaseModel):
    darwin_id: str
    browserbase_session_id: str
    purpose: str  # captcha | approval | 2fa
    ttl_seconds: int = 300


class ResolveRequest(BaseModel):
    token: str
    response: str


@router.post("/send", status_code=status.HTTP_200_OK)
async def send_notification(
    body: SendNotificationRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Send a Telegram push notification to the agent's creator.
    Called internally by the agent via the ask_human or captcha_delegate tools.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    telegram = TelegramService(settings)

    if body.action_type == "APPROVAL" and body.options:
        _pool = await get_pool()
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT up.telegram_chat_id FROM user_profiles up JOIN darwins d ON d.user_id = up.id WHERE d.id = $1",
                body.darwin_id,
            )
        if row and row["telegram_chat_id"]:
            msg_id = await telegram.send_approval_request(
                chat_id=row["telegram_chat_id"],
                message=body.message,
                approve_payload=f"approve:{body.darwin_id}",
                deny_payload=f"deny:{body.darwin_id}",
                screenshot_url=body.screenshot_url,
            )
    else:
        await telegram.send_alert_by_darwin_id(
            pool, body.darwin_id, body.message, body.screenshot_url
        )

    return {"sent": True}


@router.post("/session-link")
async def generate_session_link(
    body: SessionLinkRequest,
    agent: CurrentAgent,
    settings: SettingsDep,
) -> dict:
    """
    Generate a secure time-limited deep-link JWT for mobile CAPTCHA / 2FA handoff.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    token = mint_session_link_jwt(
        darwin_id=body.darwin_id,
        browserbase_session_id=body.browserbase_session_id,
        purpose=body.purpose,
        ttl_seconds=body.ttl_seconds,
        settings=settings,
    )
    link = f"{settings.app_base_url}/resolve?token={token}"
    expires_at = datetime.now(UTC).isoformat()
    return {"link": link, "expires_at": expires_at}


@router.post("/resolve", status_code=status.HTTP_200_OK)
async def resolve_session(
    body: ResolveRequest,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Human submits response via the mobile interstitial.
    Decodes JWT → finds hibernating agent → wakes with response context.
    """
    payload = decode_session_link_jwt(body.token, settings)
    darwin_id: str = payload["darwin_id"]

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT wake_prompt FROM hibernate_schedule WHERE darwin_id = $1 AND fired_at IS NULL ORDER BY created_at DESC LIMIT 1",
            darwin_id,
        )

    wake_prompt = (
        (row["wake_prompt"] if row else "")
        + f"\n\nHuman resolved {payload.get('purpose', 'request')}: '{body.response}'"
    )

    from workers.wake_worker import execute_agent_wake
    execute_agent_wake.delay(darwin_id, wake_prompt, f"human_resolve_{payload.get('purpose')}")

    canvas = CanvasService(pool)
    import asyncio
    asyncio.create_task(canvas.push_event(
        darwin_id=darwin_id,
        session_id=str(uuid4()),
        event_type="ASK_HUMAN_RESOLVED",
        payload={
            "type": payload.get("purpose", "general"),
            "human_response": body.response,
            "wait_time_seconds": 0,
            "compute_saved": 0.0,
        },
    ))

    telegram = TelegramService(settings)
    await telegram.send_alert_by_darwin_id(pool, darwin_id, "✅ Response received. Agent resuming.")

    return {"queued": True}


@router.post("/captcha-session", status_code=status.HTTP_200_OK)
async def captcha_session(
    body: SessionLinkRequest,
    agent: CurrentAgent,
    settings: SettingsDep,
) -> dict:
    """
    Generate a Browserbase live-stream link for human CAPTCHA solving.
    Returns the URL for the /sessions/{id}/stream WebSocket relay.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    token = mint_session_link_jwt(
        darwin_id=body.darwin_id,
        browserbase_session_id=body.browserbase_session_id,
        purpose="captcha",
        ttl_seconds=body.ttl_seconds,
        settings=settings,
    )
    stream_url = f"{settings.app_base_url}/sessions/{body.browserbase_session_id}/stream?token={token}"
    return {"stream_url": stream_url, "token": token}
