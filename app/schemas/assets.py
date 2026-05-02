"""Pydantic schemas for /assets — Digital Artifact Gateway."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class PresignedUrlRequest(BaseModel):
    agent_id: UUID
    filename: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(..., description="MIME type e.g. text/csv, application/pdf")
    size_bytes: int = Field(..., gt=0, le=500 * 1024 * 1024)  # 500 MB max


class PresignedUrlResponse(BaseModel):
    upload_url: str
    asset_ref: str = Field(..., description="Opaque ref used for indexing after upload")
    expires_in_seconds: int


class AssetIndexRequest(BaseModel):
    agent_id: UUID
    asset_ref: str
    filename: str
    content_type: str
    size_bytes: int
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class AssetRecord(BaseModel):
    asset_id: UUID
    agent_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    public_url: str
    description: str | None
    tags: list[str]
    created_at: datetime
