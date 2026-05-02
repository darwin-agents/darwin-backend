"""Memory / skill document schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SkillUploadRequest(BaseModel):
    darwin_id: str
    title: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., description="Full markdown Procedural Skill Document")
    royalty_usdc: float = Field(0.02, ge=0, le=1.0)


class SkillQueryRequest(BaseModel):
    darwin_id: str
    query: str
    top_k: int = Field(3, ge=1, le=20)


class SkillQueryResult(BaseModel):
    skill_id: UUID
    title: str
    content: str
    similarity_score: float
    author_darwin_id: UUID | None
    royalty_usdc: float


class RoyaltyRequest(BaseModel):
    skill_id: str
    consumer_darwin_id: str
