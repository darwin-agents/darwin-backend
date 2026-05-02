"""Pydantic schemas for /webhooks — External Webhook Catchers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WebhookAck(BaseModel):
    """Immediate 200 response returned to webhook senders (<10 ms)."""

    received: bool = True
    queued_job_id: str | None = None


class StripeWebhookPayload(BaseModel):
    """Stripe sends raw JSON; we validate the signature before parsing."""

    id: str
    type: str
    data: dict[str, Any]
    created: int


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict[str, Any] | None = None
    callback_query: dict[str, Any] | None = None


class EmailWebhookPayload(BaseModel):
    message_id: str
    from_address: str = Field(alias="from")
    to_address: str = Field(alias="to")
    subject: str
    body_text: str | None = None
    received_at: datetime
    agent_id: UUID | None = Field(
        None, description="Resolved by the backend from routing rules"
    )

    model_config = {"populate_by_name": True}
