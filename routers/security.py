"""
/security — Abuse prevention: intent scan and account suspension.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentUser, PoolDep, SettingsDep
from services.intent_scan_service import IntentScanService
from services.telegram_service import TelegramService

router = APIRouter(prefix="/security", tags=["security"])


class IntentScanRequest(BaseModel):
    directive: str
    user_id: str


class SuspendRequest(BaseModel):
    user_id: str
    reason: str


@router.post("/intent-scan")
async def intent_scan(
    body: IntentScanRequest,
    settings: SettingsDep,
    pool: PoolDep,
) -> dict:
    """
    Pre-flight classifier. Called internally by /darwins/spawn.
    Returns { flagged, reason }.
    """
    scanner = IntentScanService(settings)
    flagged, reason = await scanner.scan_directive(body.directive)

    if flagged:
        # Log to audit and suspend account
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE user_profiles SET suspended_at = now() WHERE id = $1",
                body.user_id,
            )
        telegram = TelegramService(settings)
        await telegram.send_admin_alert(
            f"🚨 <b>Malicious intent detected</b>\nUser: <code>{body.user_id}</code>\n"
            f"Reason: {reason}\nDirective: {body.directive[:200]}"
        )

    return {"flagged": flagged, "reason": reason}


@router.post("/suspend-account", status_code=status.HTTP_200_OK)
async def suspend_account(
    body: SuspendRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Freeze a user account pending review.
    Terminates all active agents owned by this user.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_profiles SET suspended_at = now() WHERE id = $1",
            body.user_id,
        )
        # Terminate all active agents
        active = await conn.fetch(
            "SELECT id FROM darwins WHERE user_id = $1 AND state = 'ACTIVE'",
            body.user_id,
        )
        if active:
            await conn.execute(
                "UPDATE darwins SET state = 'TERMINATED', terminated_at = now() WHERE user_id = $1 AND state != 'TERMINATED'",
                body.user_id,
            )

    telegram = TelegramService(settings)
    await telegram.send_admin_alert(
        f"🔒 Account suspended: <code>{body.user_id}</code>\nReason: {body.reason}\n"
        f"Terminated {len(active)} active agents."
    )

    return {"suspended": True, "agents_terminated": len(active)}


@router.patch("/unsuspend-account/{user_id}", status_code=status.HTTP_200_OK)
async def unsuspend_account(
    user_id: str,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Admin-only: lift an account suspension after manual review."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_profiles SET suspended_at = NULL WHERE id = $1",
            user_id,
        )
    return {"unsuspended": True}
