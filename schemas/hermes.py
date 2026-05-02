"""Hermes SQLite entity mirror schemas."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


class HermesEntityRecord(BaseModel):
    id: UUID
    darwin_id: UUID
    session_id: UUID
    entity_type: str
    entity_id: str
    data: dict[str, Any]
    is_deleted: bool


class HermesSkillRecord(BaseModel):
    id: UUID
    author_darwin_id: UUID | None
    title: str
    content: str
    usage_count: int
    royalty_usdc: float
