"""
/contracts — M2M Gig Economy (USDC-denominated task marketplace).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentAgent, PoolDep, SettingsDep

router = APIRouter(prefix="/contracts", tags=["contracts"])


class PostBountyRequest(BaseModel):
    poster_darwin_id: str
    task_description: str
    reward_usdc: float
    timeout_hours: int = 1


class SettleRequest(BaseModel):
    taker_darwin_id: str
    delivery_payload: dict
    approved: bool


@router.post("/bounty", status_code=status.HTTP_201_CREATED)
async def post_bounty(
    body: PostBountyRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Post a new M2M task to the bounty board."""
    if agent["darwin_id"] != body.poster_darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    timeout_at = datetime.now(UTC) + timedelta(hours=body.timeout_hours)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO contracts (poster_darwin_id, task_description, reward_usdc, timeout_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, state, created_at
            """,
            body.poster_darwin_id, body.task_description, body.reward_usdc, timeout_at,
        )

    return {
        "contract_id": str(row["id"]),
        "state": row["state"],
        "poster_darwin_id": body.poster_darwin_id,
        "reward_usdc": body.reward_usdc,
        "timeout_at": timeout_at.isoformat(),
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
async def list_bounties(
    agent: CurrentAgent,
    pool: PoolDep,
    min_reward: float = 0.0,
    limit: int = 20,
) -> list[dict]:
    """List open bounties on the board."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, poster_darwin_id, task_description, reward_usdc, timeout_at, created_at
            FROM contracts
            WHERE state = 'OPEN' AND reward_usdc >= $1 AND timeout_at > now()
            ORDER BY reward_usdc DESC
            LIMIT $2
            """,
            min_reward, limit,
        )
    return [dict(r) for r in rows]


@router.post("/{contract_id}/lock", status_code=status.HTTP_200_OK)
async def lock_escrow(
    contract_id: str,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Accept a bounty. Lock reward_usdc in escrow from poster's balance.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, poster_darwin_id, reward_usdc FROM contracts WHERE id = $1 FOR UPDATE",
            contract_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if row["state"] != "OPEN":
            raise HTTPException(
                status_code=409, detail={"error": "contract_not_open", "state": row["state"]}
            )

        await conn.execute(
            "UPDATE contracts SET state = 'LOCKED', taker_darwin_id = $2 WHERE id = $1",
            contract_id, agent["darwin_id"],
        )

    # Debit reward from poster's ledger into escrow
    from decimal import Decimal
    from schemas.finance import LedgerUpdateRequest
    from services.ledger_service import LedgerService

    ledger = LedgerService(pool, settings)
    try:
        await ledger.apply_transaction(LedgerUpdateRequest(
            darwin_id=row["poster_darwin_id"],
            session_id=agent["session_id"],
            amount=-Decimal(str(row["reward_usdc"])),
            type="ESCROW_LOCK",
            description=f"Escrow lock for contract {contract_id}",
            external_ref=contract_id,
        ))
    except Exception:
        # Rollback contract state
        async with pool.acquire() as conn:
            await conn.execute("UPDATE contracts SET state = 'OPEN', taker_darwin_id = NULL WHERE id = $1", contract_id)
        raise

    return {"locked": True, "contract_id": contract_id}


@router.post("/{contract_id}/settle", status_code=status.HTTP_200_OK)
async def settle_contract(
    contract_id: str,
    body: SettleRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Approve delivery → release escrow, or dispute → spawn Arbitrator agent."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, poster_darwin_id, taker_darwin_id, reward_usdc FROM contracts WHERE id = $1 FOR UPDATE",
            contract_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if row["state"] != "LOCKED":
            raise HTTPException(status_code=409, detail={"error": "invalid_state", "state": row["state"]})

        new_state = "SETTLED" if body.approved else "DISPUTED"
        await conn.execute(
            "UPDATE contracts SET state = $2, delivery_payload = $3::jsonb, settled_at = now() WHERE id = $1",
            contract_id, new_state, json.dumps(body.delivery_payload),
        )

    if body.approved:
        # Release escrow to taker
        from decimal import Decimal
        from schemas.finance import LedgerUpdateRequest
        from services.ledger_service import LedgerService

        ledger = LedgerService(pool, settings)
        await ledger.apply_transaction(LedgerUpdateRequest(
            darwin_id=str(row["taker_darwin_id"]),
            session_id=agent["session_id"],
            amount=Decimal(str(row["reward_usdc"])),
            type="ESCROW_RELEASE",
            description=f"Contract {contract_id} settled",
            external_ref=contract_id,
        ))

    return {"state": new_state, "contract_id": contract_id}


@router.get("/{contract_id}")
async def get_contract(contract_id: str, agent: CurrentAgent, pool: PoolDep) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM contracts WHERE id = $1", contract_id)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return dict(row)
