"""Pydantic schemas for /identity — Reputation Oracle."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TrustScore(BaseModel):
    agent_id: UUID
    score: float = Field(..., ge=0.0, le=100.0)
    total_contracts: int
    successful_contracts: int
    disputed_contracts: int
    last_updated: datetime


class DisputeRequest(BaseModel):
    contract_id: UUID
    claimant_agent_id: UUID
    respondent_agent_id: UUID
    evidence: str = Field(..., min_length=10)


class DisputeResponse(BaseModel):
    dispute_id: UUID
    contract_id: UUID
    status: str
    arbitrator_agent_id: UUID | None
    verdict: str | None
    created_at: datetime
    resolved_at: datetime | None
