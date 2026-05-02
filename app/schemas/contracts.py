"""Pydantic schemas for /contracts — M2M Marketplace."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ContractStatus(str, Enum):
    OPEN = "OPEN"
    LOCKED = "LOCKED"    # escrow active
    DELIVERED = "DELIVERED"
    SETTLED = "SETTLED"
    DISPUTED = "DISPUTED"
    EXPIRED = "EXPIRED"


class PostBountyRequest(BaseModel):
    poster_agent_id: UUID
    task_description: str = Field(..., min_length=10)
    reward_usdc: float = Field(..., gt=0)
    timeout: str = Field(..., description="ISO 8601 duration e.g. PT1H")
    required_skills: list[str] = Field(default_factory=list)
    max_bids: int = Field(1, ge=1, le=10)


class BountyResponse(BaseModel):
    contract_id: UUID
    status: ContractStatus
    poster_agent_id: UUID
    reward_usdc: float
    timeout: str
    created_at: datetime
    expires_at: datetime


class LockEscrowRequest(BaseModel):
    contract_id: UUID
    executor_agent_id: UUID


class SettleContractRequest(BaseModel):
    contract_id: UUID
    approver_agent_id: UUID
    approved: bool
    feedback: str | None = None


class ContractDetail(BaseModel):
    contract_id: UUID
    status: ContractStatus
    poster_agent_id: UUID
    executor_agent_id: UUID | None
    reward_usdc: float
    task_description: str
    created_at: datetime
    settled_at: datetime | None
