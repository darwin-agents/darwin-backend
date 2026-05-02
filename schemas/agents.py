"""Agent lifecycle schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

AgentState = Literal["PENDING", "ACTIVE", "HIBERNATING", "TERMINATED"]


class SpawnRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    directive: str = Field(..., min_length=10, description="Initial mission — scanned for abuse")
    llm_model: str = Field(default="claude-sonnet-4-6")
    initial_budget: float = Field(..., gt=0, le=10_000)
    vcc_ref: str = Field(..., description="opaque reference from POST /users/{uid}/vault")
    tool_set: list[str] = Field(default_factory=list)
    requires_approval: bool = True


class SpawnResponse(BaseModel):
    darwin_id: UUID
    session_id: UUID
    state: AgentState


class HibernateRequest(BaseModel):
    session_id: UUID
    reason: str
    wake_trigger: dict = Field(
        ...,
        description='{"type":"CRON","trigger_at":"ISO8601"} or {"type":"WEBHOOK","webhook_key":"str"}',
    )
    browserbase_session_id: str | None = None
    episodic_summary: str = Field(
        ..., max_length=3000, description="Compressed 200-token session narrative"
    )


class UplinkRequest(BaseModel):
    message: str
    attachments: list[dict[str, Any]] = []


class ConfigPatchRequest(BaseModel):
    requires_approval: bool | None = None
    budget_injection: float | None = Field(None, gt=0)


class AgentStateResponse(BaseModel):
    state: AgentState
    ledger_balance: float
    burn_rate_per_hour: float
    current_session_id: UUID | None
    last_event_at: datetime | None


class AgentSummary(BaseModel):
    id: UUID
    name: str
    state: AgentState
    ledger_balance: float
    initial_budget: float
    total_revenue: float
    token_roi: float | None
    last_active_at: datetime | None
    current_session_id: UUID | None


class SwarmStateResponse(BaseModel):
    darwins: list[AgentSummary]
    total_revenue: float
    total_burned: float
