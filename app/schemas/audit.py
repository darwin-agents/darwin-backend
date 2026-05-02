"""Pydantic schemas for /audit — Compliance Engine."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    FINANCIAL_DECISION = "FINANCIAL_DECISION"
    TOOL_INVOCATION = "TOOL_INVOCATION"
    SPAWN = "SPAWN"
    TERMINATE = "TERMINATE"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    ABUSE_FLAGGED = "ABUSE_FLAGGED"


class AuditLogRequest(BaseModel):
    agent_id: UUID
    event_type: AuditEventType
    prompt_snapshot: str | None = Field(
        None, description="Truncated active prompt at time of decision"
    )
    decision: str = Field(..., description="What the agent decided to do")
    context_summary: str
    financial_impact_usd: float | None = None
    metadata: dict = Field(default_factory=dict)


class AuditLogEntry(BaseModel):
    log_id: UUID
    agent_id: UUID
    event_type: AuditEventType
    decision: str
    financial_impact_usd: float | None
    logged_at: datetime


class TaxExportRequest(BaseModel):
    owner_id: str
    fiscal_year: int = Field(..., ge=2024, le=2100)
    include_agents: list[UUID] | None = None
    format: str = Field("json", pattern="^(json|csv)$")


class TaxExportResponse(BaseModel):
    owner_id: str
    fiscal_year: int
    total_revenue_usd: float
    total_compute_costs_usd: float
    net_profit_usd: float
    download_url: str | None = None
    generated_at: datetime
