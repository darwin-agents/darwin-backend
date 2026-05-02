"""Pydantic schemas for /swarms — Swarm Orchestrator."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateSwarmRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    owner_id: str
    ceo_agent_id: UUID = Field(..., description="The managing agent with spawn/terminate rights")
    initial_treasury_usd: float = Field(..., gt=0)
    description: str | None = None


class SwarmResponse(BaseModel):
    swarm_id: UUID
    name: str
    ceo_agent_id: UUID
    member_count: int
    treasury_usd: float
    created_at: datetime


class AllocateTreasuryRequest(BaseModel):
    swarm_id: UUID
    target_agent_id: UUID
    amount_usd: float = Field(..., gt=0)
    reason: str | None = None


class SwarmMembersResponse(BaseModel):
    swarm_id: UUID
    members: list[dict]
    total_budget_allocated_usd: float
    total_revenue_usd: float
