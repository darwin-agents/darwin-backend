"""
/firmware — Evolutionary Engine

Live-upgrade digital organisms without downtime.
DNA patches and SQLite schema migrations are injected on next wake-up cycle.
"""

from fastapi import APIRouter, status

from app.core.dependencies import CurrentUser, SupabaseDep
from app.schemas.firmware import (
    FirmwareUpgradeRequest,
    FirmwareUpgradeResponse,
    MemoryMigrationRequest,
    MemoryMigrationResponse,
)

router = APIRouter(prefix="/firmware", tags=["firmware"])


@router.post("/upgrade", response_model=FirmwareUpgradeResponse, status_code=status.HTTP_202_ACCEPTED)
async def upgrade_firmware(
    body: FirmwareUpgradeRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> FirmwareUpgradeResponse:
    """
    Roll out a new Hermes container image to hibernating agents.

    Scans the database for agents in target_states, updates their Modal
    provisioning profile's Docker image tag. On next wake-up they boot
    into the new cognitive framework automatically.
    """
    raise NotImplementedError


@router.post("/migrate", response_model=MemoryMigrationResponse, status_code=status.HTTP_202_ACCEPTED)
async def migrate_memory(
    body: MemoryMigrationRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> MemoryMigrationResponse:
    """
    Queue a SQLite schema migration for agents on their next wake-up.

    Injects a lightweight Python migration script into the agent's Modal Volume
    so historical memories survive the brain upgrade.
    """
    raise NotImplementedError
