"""
/identity — Reputation Oracle (trust scores and dispute resolution).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentAgent, CurrentUser, PoolDep

router = APIRouter(prefix="/identity", tags=["identity"])


class DisputeRequest(BaseModel):
    contract_id: str
    claimant_darwin_id: str
    evidence: str


@router.get("/{darwin_id}/score")
async def get_trust_score(
    darwin_id: str,
    user: CurrentUser,
    pool: PoolDep,
) -> dict:
    """Aggregated trust score from past M2M contract performance."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE state = 'SETTLED')   AS completed,
              COUNT(*) FILTER (WHERE state = 'DISPUTED')  AS disputed,
              COUNT(*)                                      AS total
            FROM contracts
            WHERE poster_darwin_id = $1 OR taker_darwin_id = $1
            """,
            darwin_id,
        )

    total = int(row["total"] or 0)
    completed = int(row["completed"] or 0)
    disputed = int(row["disputed"] or 0)

    score = round((completed / total * 100) if total > 0 else 100.0, 2)

    return {
        "darwin_id": darwin_id,
        "trust_score": score,
        "contracts_completed": completed,
        "disputes_filed": disputed,
        "total_contracts": total,
    }


@router.post("/dispute", status_code=status.HTTP_201_CREATED)
async def file_dispute(
    body: DisputeRequest,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """
    File a quality dispute on a completed contract.
    Triggers an Arbitrator agent evaluation (Phase 2).
    """
    if agent["darwin_id"] != body.claimant_darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE contracts SET state = 'DISPUTED' WHERE id = $1",
            body.contract_id,
        )

    # TODO Phase 2: spawn Arbitrator agent with delivery_payload + evidence
    return {
        "dispute_id": body.contract_id,
        "status": "DISPUTED",
        "message": "Arbitration queued.",
    }
