"""Pydantic schemas for /gateway — Resource Gateway (LLM Multiplexer)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InferenceRequest(BaseModel):
    agent_id: UUID
    model: str = Field(..., description="e.g. 'gpt-4o', 'claude-3-5-sonnet'")
    messages: list[dict]
    max_tokens: int = Field(4096, ge=1, le=128_000)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    stream: bool = False
    priority: int = Field(5, ge=1, le=10, description="Higher = routed first under load")


class InferenceResponse(BaseModel):
    request_id: UUID
    content: str
    model_used: str
    tokens_used: int
    cost_usd: float
    latency_ms: int


class GatewayLimitsResponse(BaseModel):
    """Real-time API weather for a swarm CEO to make hibernation decisions."""

    timestamp: datetime
    providers: list[dict]
    global_requests_per_minute_remaining: int
    recommended_action: str = Field(
        ..., description="'proceed' | 'throttle' | 'hibernate_30m'"
    )
