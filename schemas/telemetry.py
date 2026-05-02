"""Telemetry and canvas event schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, Field


class MetricsPayload(BaseModel):
    tokens_burned_last_30s: int = Field(..., ge=0)
    llm_cost_last_30s: float = Field(..., ge=0)
    compute_cost_last_30s: float = Field(..., ge=0)
    failed_http_requests_last_30s: int = Field(..., ge=0)
    memory_util_mb: int = Field(..., ge=0)
    current_task: str


class TelemetryStreamRequest(BaseModel):
    darwin_id: str
    session_id: str
    metrics: MetricsPayload


VALID_EVENT_TYPES = frozenset({
    # Agent lifecycle
    "AGENT_SPAWNED", "AGENT_WOKE", "AGENT_HIBERNATED", "AGENT_TERMINATED",
    "AGENT_KILLED", "SESSION_ENDED",

    # Cognitive events
    "THOUGHT", "LLM_INFERENCE",

    # Tool layer
    "TOOL_CALL", "TOOL_RESULT",

    # Sub-agents (neurons + delegate_task)
    "NEURON_SPAWNED", "NEURON_COMPLETED", "NEURON_FAILED", "SYNAPTIC_INTEGRATION",
    "DELEGATE_SPAWNED", "DELEGATE_COMPLETED",

    # Code execution
    "CODE_EXEC_START", "CODE_EXEC_END",

    # Browser & vision
    "BROWSER_FETCH", "BROWSER_SESSION_OPEN", "BROWSER_SCREENSHOT",
    "BROWSER_CLICK", "BROWSER_TYPE", "BROWSER_SESSION_CLOSE",

    # Finance
    "LEDGER_DEBIT", "LEDGER_CREDIT", "VCC_INJECTED", "USDC_TX",

    # Human-in-the-loop
    "ASK_HUMAN_SENT", "ASK_HUMAN_RESOLVED",
    "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "APPROVAL_DENIED",
    "CAPTCHA_DELEGATED", "CAPTCHA_SOLVED",

    # Memory
    "SKILL_CREATED", "SKILL_LOADED", "SKILL_EXECUTED",
    "EPISODIC_SUMMARY_WRITTEN", "MEMORY_COMPRESSED",
    "ENTITY_CREATED", "ENTITY_UPDATED",

    # Safety & telemetry
    "CIRCUIT_BREAKER_TRIPPED",
    "PROVIDER_ROTATED",

    # Scheduling
    "CRON_SCHEDULED", "CRON_CANCELLED", "CRON_FIRED",

    # Control plane
    "UPLINK_INJECTED", "CONFIG_UPDATED",
})


class CanvasEventRequest(BaseModel):
    darwin_id: str
    session_id: str
    neuron_id: str | None = None
    parent_event_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    timestamp_ms: int
    token_cost: float = Field(default=0.0, ge=0)
    fiat_cost: float = Field(default=0.0, ge=0)

    def validate_event_type(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"Unknown event_type: {self.event_type}")


class EntityPushRequest(BaseModel):
    darwin_id: str
    session_id: str
    entity_type: str
    entity_id: str
    data: dict[str, Any]
    operation: str = Field(..., pattern="^(CREATE|UPDATE|DELETE)$")


class CircuitBreakerTripRequest(BaseModel):
    darwin_id: str
    trigger: str = Field(
        ..., description="metabolic_runaway | credential_stuffing | ddos | captcha_loop"
    )
    evidence: dict[str, Any] = Field(default_factory=dict)


class LLMCallRequest(BaseModel):
    darwin_id: str
    session_id: str
    model: str
    provider: str = "anthropic"
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(..., ge=0)
    duration_ms: int = Field(default=0, ge=0)
    turn_id: str | None = None


class TelemetryHistoryBucket(BaseModel):
    timestamp: int  # Unix ms, start of bucket
    tokens_burned: int
    llm_cost: float
    compute_cost: float
    revenue: float
