"""Pydantic schemas for /firmware — Evolutionary Engine."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FirmwareUpgradeRequest(BaseModel):
    new_version: str = Field(..., description="e.g. 'hermes-v2.1'")
    docker_image_tag: str
    release_notes: str | None = None
    target_states: list[str] = Field(
        default=["HIBERNATING"],
        description="Only upgrade agents in these states",
    )
    rollout_percentage: float = Field(
        100.0, ge=1.0, le=100.0, description="Canary rollout percentage"
    )


class FirmwareUpgradeResponse(BaseModel):
    upgrade_id: UUID
    new_version: str
    agents_targeted: int
    started_at: datetime


class MemoryMigrationRequest(BaseModel):
    upgrade_id: UUID
    migration_script: str = Field(
        ..., description="Python migration script injected into agent volume on next wake-up"
    )
    affected_schema_version: str


class MemoryMigrationResponse(BaseModel):
    migration_id: UUID
    upgrade_id: UUID
    agents_queued: int
    created_at: datetime
