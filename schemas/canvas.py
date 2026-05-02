"""Canvas event payload shapes — one class per event_type.

These are the typed shapes the frontend's Vue components expect in darwin_events.payload.
The actual DB column is JSONB; validation happens at the application layer before insert.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class AgentSpawnedPayload(BaseModel):
    name: str
    initial_budget: float
    llm_model: str
    system_prompt_preview: str
    tool_set: list[str]


class AgentWokePayload(BaseModel):
    wake_reason: str
    session_id: str
    balance_at_wake: float
    hibernated_for_hours: float


class AgentHibernatedPayload(BaseModel):
    reason: str
    wake_trigger_type: str
    wake_trigger_at: str | None
    episodic_summary: str
    browserbase_session_kept_alive: bool


class AgentTerminatedPayload(BaseModel):
    reason: str
    final_balance: float
    total_revenue: float
    total_burned: float
    token_roi: float | None


# ── Cognition ─────────────────────────────────────────────────────────────────

class ThoughtPayload(BaseModel):
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ToolCallPayload(BaseModel):
    tool_name: str
    input: dict[str, Any]
    status: str  # pending | success | failure | retry


class ToolResultPayload(BaseModel):
    tool_name: str
    status: str
    output_preview: str
    latency_ms: int


class BrowserScreenshotPayload(BaseModel):
    screenshot_url: str
    viewport_description: str
    coordinates_mapped: bool = False


# ── Sub-agents ────────────────────────────────────────────────────────────────

class NeuronSpawnedPayload(BaseModel):
    neuron_id: str
    task: str
    parent_event_id: str


class NeuronCompletedPayload(BaseModel):
    neuron_id: str
    result_summary: str
    tokens_burned: int
    cost: float


class NeuronFailedPayload(BaseModel):
    neuron_id: str
    reason: str
    tokens_burned: int
    cost: float


class SynapticIntegrationPayload(BaseModel):
    neuron_ids_merged: list[str]
    failed_neurons: list[str]
    synthesis: str


# ── Finance ───────────────────────────────────────────────────────────────────

class LedgerEventPayload(BaseModel):
    amount: float
    balance_after: float
    type: str
    description: str
    external_ref: str | None = None


# ── Memory ────────────────────────────────────────────────────────────────────

class SkillCreatedPayload(BaseModel):
    skill_id: str
    title: str
    content_preview: str
    royalty_usdc: float


class SkillLoadedPayload(BaseModel):
    skill_id: str
    title: str
    similarity_score: float
    author_darwin_id: str | None


class EpisodicSummaryPayload(BaseModel):
    session_id: str
    summary: str
    raw_tokens_compressed: int
    summary_tokens: int


# ── Human-in-the-loop ─────────────────────────────────────────────────────────

class AskHumanSentPayload(BaseModel):
    prompt: str
    screenshot_url: str | None
    timeout_seconds: int
    type: str  # 2fa | captcha | general


class AskHumanResolvedPayload(BaseModel):
    type: str
    human_response: str
    wait_time_seconds: int
    compute_saved: float


class CaptchaDelegatedPayload(BaseModel):
    captcha_type: str
    attempts_before_delegation: int
    cost_burned_on_attempts: float
    session_link: str


class ApprovalRequestedPayload(BaseModel):
    amount: float
    justification: str
    token_roi_estimate: str | None


# ── Entities ──────────────────────────────────────────────────────────────────

class EntityEventPayload(BaseModel):
    entity_type: str
    entity_id: str
    data: dict[str, Any]
    operation: str  # CREATE | UPDATE | DELETE
