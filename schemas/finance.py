"""Finance schemas with Luhn validation."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TransactionType = Literal[
    "INITIAL_FUND",
    "VCC_DEBIT",
    "REVENUE_CREDIT",
    "ROYALTY_DEBIT",
    "ROYALTY_CREDIT",
    "ESCROW_LOCK",
    "ESCROW_RELEASE",
    "METABOLIC_INJECTION",
    "WITHDRAWAL",
    "LLM_USAGE_DEBIT",
]


def _luhn_check(pan: str) -> bool:
    digits = [int(d) for d in pan if d.isdigit()]
    if len(digits) != 16:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d if i % 2 == 0 else sum(divmod(d * 2, 10))
    return total % 10 == 0


class VCCPayload(BaseModel):
    pan: str
    cvv: str
    expiry: str
    spend_limit: float = Field(..., gt=0, le=50_000)
    provider: Literal["privacy.com", "revolut", "lithic", "stripe_issuing", "manual"] = "manual"

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, v: str) -> str:
        digits = re.sub(r"\s|-", "", v)
        if not _luhn_check(digits):
            raise ValueError("PAN fails Luhn check or is not 16 digits")
        return digits

    @field_validator("cvv")
    @classmethod
    def validate_cvv(cls, v: str) -> str:
        if not re.match(r"^\d{3,4}$", v):
            raise ValueError("CVV must be 3 or 4 digits")
        return v

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, v: str) -> str:
        if not re.match(r"^\d{2}/\d{2}$", v):
            raise ValueError("Expiry must be MM/YY")
        return v


class VaultSubmitRequest(BaseModel):
    darwin_id: str
    secret_type: Literal["vcc", "stripe_key", "broker_api_key", "iban"]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_vcc(self) -> "VaultSubmitRequest":
        if self.secret_type == "vcc":
            VCCPayload(**self.payload)
        return self


class VaultSubmitResponse(BaseModel):
    ref: str
    spend_limit: float


class LedgerUpdateRequest(BaseModel):
    darwin_id: str
    session_id: str
    amount: Decimal  # negative = debit, positive = credit
    type: TransactionType
    description: str
    external_ref: str | None = None


class LedgerUpdateResult(BaseModel):
    transaction_id: str
    new_balance: Decimal


class InjectPaymentRequest(BaseModel):
    darwin_id: str
    vcc_ref: str
    browserbase_session_id: str
    target_fields: dict[str, str] = Field(
        ..., description="{pan_element, cvv_element, expiry_element} CSS selectors"
    )


class LedgerResponse(BaseModel):
    darwin_id: str
    current_balance: float
    initial_budget: float
    transactions: list[dict]
    total_revenue: float
    total_burned: float


class WithdrawRequest(BaseModel):
    amount: float = Field(..., gt=0)
    destination: str = Field(..., description="Stripe Connect account ID")


class MicroTxRequest(BaseModel):
    tx_hash: str
    from_darwin_id: str
    to_darwin_id: str
    amount_usdc: float = Field(..., gt=0)
