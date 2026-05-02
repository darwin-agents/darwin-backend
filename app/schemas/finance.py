"""Pydantic schemas for /finance — Internal Finance & Ledger."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class TransactionType(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    ROYALTY = "ROYALTY"
    ESCROW_LOCK = "ESCROW_LOCK"
    ESCROW_RELEASE = "ESCROW_RELEASE"


class MintCardRequest(BaseModel):
    agent_id: UUID
    spend_limit_usd: float = Field(..., gt=0, le=10_000)
    label: str | None = None


class MintCardResponse(BaseModel):
    vcc_ref: str = Field(..., description="Opaque reference token; raw PAN never exposed")
    agent_id: UUID
    spend_limit_usd: float
    issued_at: datetime


class LedgerUpdateRequest(BaseModel):
    agent_id: UUID
    amount_usd: float = Field(..., description="Positive = credit, negative = debit")
    tx_type: TransactionType
    description: str
    merchant: str | None = None
    contract_id: UUID | None = None


class LedgerUpdateResponse(BaseModel):
    tx_id: UUID
    agent_id: UUID
    new_balance_usd: float
    tx_type: TransactionType
    timestamp: datetime


class InjectPaymentRequest(BaseModel):
    """Agent requests JIT VCC injection into a Browserbase session."""

    agent_id: UUID
    vcc_ref: str
    browserbase_session_id: str
    target_element_id: str
    merchant_name: str
    amount_usd: float = Field(..., gt=0)


class BalanceResponse(BaseModel):
    agent_id: UUID
    balance_usd: float
    spend_limit_usd: float
    reserved_usd: float  # locked in escrow
    available_usd: float
