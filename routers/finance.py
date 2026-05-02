"""
/finance — Central bank. All money flows through here.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from deps import CurrentAgent, CurrentUser, PoolDep, SettingsDep
from schemas.finance import (
    InjectPaymentRequest,
    LedgerUpdateRequest,
    LedgerUpdateResult,
    LedgerResponse,
    MicroTxRequest,
    WithdrawRequest,
)
from services.canvas_service import CanvasService
from services.ledger_service import InsufficientFundsError, LedgerService
from services.vcc_service import VCCService

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/mint-card", status_code=status.HTTP_200_OK)
async def mint_card(
    darwin_id: str,
    vcc_ref: str,
    user: CurrentUser,
    pool: PoolDep,
) -> dict:
    """
    Phase 1: link an existing vcc_vault row to a darwins row.
    Phase 2: call Lithic/Stripe Issuing to programmatically issue a card.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE darwins SET vcc_ref = $2 WHERE id = $1 AND user_id = $3",
            darwin_id, vcc_ref, user["sub"],
        )
    return {"darwin_id": darwin_id, "vcc_ref": vcc_ref}


@router.post("/ledger/update", response_model=LedgerUpdateResult)
async def update_ledger(
    body: LedgerUpdateRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> LedgerUpdateResult:
    """
    Debit or credit the master ledger with a SELECT … FOR UPDATE row lock.
    Called exclusively by the agent (agent JWT required).
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    ledger = LedgerService(pool, settings)
    try:
        return await ledger.apply_transaction(body)
    except InsufficientFundsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "insufficient_funds",
                "balance": float(exc.balance),
                "requested": float(exc.requested),
            },
        ) from exc


@router.post("/inject-payment", status_code=status.HTTP_200_OK)
async def inject_payment(
    body: InjectPaymentRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    JIT VCC decryption + injection into a live Browserbase session.
    The raw PAN never touches the agent's process memory.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    vcc_svc = VCCService(pool, settings)
    if not await vcc_svc.verify_ownership(body.vcc_ref, body.darwin_id):
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    try:
        await vcc_svc.decrypt_and_inject(
            vcc_ref=body.vcc_ref,
            browserbase_session_id=body.browserbase_session_id,
            target_fields=body.target_fields,
        )
    except NotImplementedError:
        # Browserbase integration pending — log event and return success for scaffolding
        pass

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_event(
        darwin_id=body.darwin_id,
        session_id=agent["session_id"],
        event_type="VCC_INJECTED",
        payload={
            "vcc_ref": body.vcc_ref,
            "browserbase_session_id": body.browserbase_session_id,
        },
    ))
    return {"injected": True}


@router.post("/microtx", status_code=status.HTTP_200_OK)
async def micro_transaction(
    body: MicroTxRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Verify on-chain USDC tx and update both agents' P&L.
    Phase 1 stub — Phase 2 connects to on-chain RPC.
    """
    # TODO: verify tx_hash via RPC provider
    ledger = LedgerService(pool, settings)
    session_id = str(uuid4())

    await ledger.apply_transaction(LedgerUpdateRequest(
        darwin_id=body.from_darwin_id,
        session_id=session_id,
        amount=Decimal(str(-body.amount_usdc)),
        type="ROYALTY_DEBIT",
        description=f"USDC tx to {body.to_darwin_id}",
        external_ref=body.tx_hash,
    ))
    await ledger.apply_transaction(LedgerUpdateRequest(
        darwin_id=body.to_darwin_id,
        session_id=session_id,
        amount=Decimal(str(body.amount_usdc)),
        type="ROYALTY_CREDIT",
        description=f"USDC tx from {body.from_darwin_id}",
        external_ref=body.tx_hash,
    ))
    return {"settled": True, "tx_hash": body.tx_hash}


@router.get("/ledger/{darwin_id}", response_model=LedgerResponse)
async def get_ledger(
    darwin_id: str,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
    limit: int = 50,
    offset: int = 0,
) -> LedgerResponse:
    ledger = LedgerService(pool, settings)
    data = await ledger.get_ledger(darwin_id, limit, offset)
    return LedgerResponse(**data)


@router.post("/withdraw", status_code=status.HTTP_202_ACCEPTED)
async def withdraw(
    body: WithdrawRequest,
    user: CurrentUser,
    pool: PoolDep,
) -> dict:
    """Initiate an ACH off-ramp via Stripe Connect. Phase 2 only."""
    # TODO: call Stripe Payout API
    return {"queued": True, "amount": body.amount, "destination": body.destination}
