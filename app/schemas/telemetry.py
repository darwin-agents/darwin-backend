"""Pydantic schemas for /telemetry — Metabolic Streaming & Anomaly Detection."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MetabolicSample(BaseModel):
    """Single telemetry snapshot streamed from a Modal container."""

    agent_id: UUID
    timestamp: datetime
    tokens_per_minute: float = Field(..., ge=0)
    http_requests_per_minute: float = Field(..., ge=0)
    consecutive_failures: int = Field(..., ge=0)
    memory_mb: float = Field(..., ge=0)
    burn_rate_usd_per_hour: float = Field(..., ge=0)
    current_domain: str | None = None


class TelemetryStreamRequest(BaseModel):
    samples: list[MetabolicSample] = Field(..., min_length=1, max_length=100)


class CircuitBreakerTripRequest(BaseModel):
    agent_id: UUID
    trigger: str = Field(
        ..., description="'metabolic_runaway' | 'credential_stuffing' | 'ddos'"
    )
    evidence: dict = Field(default_factory=dict)


class CircuitBreakerTripResponse(BaseModel):
    agent_id: UUID
    tripped: bool
    action_taken: str
    notification_sent: bool


class TelemetryDashboard(BaseModel):
    agent_id: UUID
    current_state: str
    avg_burn_rate_usd_per_hour: float
    total_tokens_consumed: int
    total_revenue_usd: float
    token_roi: float = Field(..., description="Revenue generated per dollar of tokens spent")
    anomaly_flags: list[str]
    last_updated: datetime
