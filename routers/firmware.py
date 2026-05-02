"""
/firmware — Evolutionary Engine (live upgrades for hibernating agents).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentUser, PoolDep, SettingsDep

router = APIRouter(prefix="/firmware", tags=["firmware"])


class UpgradeRequest(BaseModel):
    image_tag: str
    target: str = "all_hibernating"  # "all_hibernating" | [uuid, ...]
    target_ids: list[str] | None = None


class MigrateRequest(BaseModel):
    migration_sql: str
    target_darwin_ids: list[str] | None = None


@router.post("/upgrade", status_code=status.HTTP_202_ACCEPTED)
async def upgrade_firmware(
    body: UpgradeRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Update the Docker image tag for hibernating agents.
    Applied on next wake-up — agents boot into the new Hermes version automatically.
    """
    import redis.asyncio as aioredis
    from services.modal_service import ModalService

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)

    if body.target == "all_hibernating":
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM darwins WHERE user_id = $1 AND state = 'HIBERNATING'",
                user["sub"],
            )
        target_ids = [str(r["id"]) for r in rows]
    else:
        target_ids = body.target_ids or []

    for darwin_id in target_ids:
        await modal.update_image_tag(darwin_id, body.image_tag)

    return {
        "image_tag": body.image_tag,
        "agents_targeted": len(target_ids),
        "message": "Image tag updated. Will be applied on next wake-up.",
    }


@router.post("/migrate", status_code=status.HTTP_202_ACCEPTED)
async def migrate_memory(
    body: MigrateRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Queue a SQLite schema migration to run inside the agent's Modal Volume on next wake-up.
    Stored in Redis; the wake_worker injects it before the Hermes runtime boots.
    """
    import redis.asyncio as aioredis

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    if body.target_darwin_ids:
        targets = body.target_darwin_ids
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM darwins WHERE user_id = $1 AND state = 'HIBERNATING'",
                user["sub"],
            )
        targets = [str(r["id"]) for r in rows]

    for darwin_id in targets:
        await redis.set(f"migration:{darwin_id}", body.migration_sql)

    return {
        "agents_queued": len(targets),
        "message": "Migration queued. Will run on next agent wake-up.",
    }
