"""
/finance — Internal Finance Controller

Central bank of the swarm. Manages the master ledger, VCC lifecycle,
and JIT payment injection with row-level locking.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, PgPoolDep, SupabaseDep
from app.core.exceptions import InsufficientFunds
from app.schemas.finance import (
    BalanceResponse,
    InjectPaymentRequest,
    LedgerUpdateRequest,
    LedgerUpdateResponse,
    MintCardRequest,
    MintCardResponse,
)

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/mint-card", response_model=MintCardResponse, status_code=status.HTTP_201_CREATED)
async def mint_card(
    body: MintCardRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> MintCardResponse:
    """
    Mint a Virtual Credit Card via Lithic/Stripe Issuing.

    Raw PAN/CVV are immediately encrypted (AES-256-GCM) and stored in
    Supabase Vault. Only the opaque vcc_ref is returned.
    """
    raise NotImplementedError


@router.post("/ledger/update", response_model=LedgerUpdateResponse)
async def update_ledger(
    body: LedgerUpdateRequest,
    _agent: CurrentAgent,
    _pool: PgPoolDep,
) -> LedgerUpdateResponse:
    """
    Atomically update the master ledger with a SELECT … FOR UPDATE row lock.

    Prevents double-spend when an agent fires concurrent purchase requests.
    """
    raise NotImplementedError


@router.get("/{agent_id}/balance", response_model=BalanceResponse)
async def get_balance(
    agent_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> BalanceResponse:
    raise NotImplementedError


@router.post("/inject-payment", status_code=status.HTTP_200_OK)
async def inject_payment(
    body: InjectPaymentRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> dict:
    """
    JIT VCC injection into a live Browserbase session.

    Flow:
    1. Validate the agent owns this vcc_ref.
    2. Decrypt PAN in active server memory only.
    3. Push raw digits directly into the Browserbase DOM via secure WebSocket.
    4. Immediately purge the decrypted string from RAM.
    5. Return success; the agent never sees its own card number.
    """
    raise NotImplementedError
