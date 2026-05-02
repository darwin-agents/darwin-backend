"""Pydantic schemas for /memory — Genetic Repository (Skill Documents + Vector Search)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SkillUploadRequest(BaseModel):
    agent_id: UUID
    title: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., description="Markdown Procedural Skill Document")
    tags: list[str] = Field(default_factory=list)
    public: bool = Field(True, description="If True, shared across the swarm")


class SkillDocument(BaseModel):
    skill_id: UUID
    title: str
    content: str
    author_agent_id: UUID
    tags: list[str]
    usage_count: int
    royalty_earned_usdc: float
    created_at: datetime
    updated_at: datetime


class MemoryQueryRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    top_k: int = Field(5, ge=1, le=20)
    requester_agent_id: UUID


class MemoryQueryResult(BaseModel):
    skill_id: UUID
    title: str
    content: str
    similarity_score: float
    author_agent_id: UUID


class MemoryQueryResponse(BaseModel):
    results: list[MemoryQueryResult]
    query: str


class RoyaltyRequest(BaseModel):
    skill_id: UUID
    consuming_agent_id: UUID
    amount_usdc: float = Field(..., gt=0)
