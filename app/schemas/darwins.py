"""Pydantic schemas for /darwins — Agent Lifecycle Manager."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    HIBERNATING = "HIBERNATING"
    TERMINATED = "TERMINATED"


class SpawnAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    directive: str = Field(..., description="Initial mission directive for the agent")
    budget_usd: float = Field(..., gt=0, le=10_000, description="Initial fiat budget in USD")
    wake_up_cron: str | None = Field(None, description="Cron expression for scheduled wake-ups")
    owner_id: str = Field(..., description="UUID of the human creator")
    swarm_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class SpawnAgentResponse(BaseModel):
    agent_id: UUID
    state: AgentState
    modal_container_id: str | None
    created_at: datetime
    vcc_ref: str = Field(..., description="Reference token for the agent's VCC")


class AgentStateResponse(BaseModel):
    agent_id: UUID
    name: str
    state: AgentState
    budget_remaining_usd: float
    burn_rate_usd_per_hour: float
    last_active_at: datetime | None
    next_wake_at: datetime | None


class HibernateRequest(BaseModel):
    agent_id: UUID
    reason: str
    wake_trigger: str = Field(
        ..., description="'cron:<expression>' | 'webhook:<event>' | 'manual'"
    )
    wake_at: datetime | None = None
    state_summary: str | None = Field(None, description="Compressed episodic memory snapshot")


class TerminateRequest(BaseModel):
    agent_id: UUID
    reason: str = Field(..., description="Human-readable termination reason for audit trail")
    initiated_by: str = Field(..., description="'platform_anomaly' | 'kill_switch' | 'user'")


class AgentListResponse(BaseModel):
    agents: list[AgentStateResponse]
    total: int
