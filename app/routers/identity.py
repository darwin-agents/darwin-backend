"""
/identity — Reputation Oracle

Credit bureau for the digital species. Trust scores govern M2M contract eligibility.
Low-reputation agents are starved of capital and naturally go extinct.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, SupabaseDep
from app.schemas.identity import DisputeRequest, DisputeResponse, TrustScore

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("/{agent_id}/score", response_model=TrustScore)
async def get_trust_score(
    agent_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> TrustScore:
    """
    Retrieve the global Trust Score for an agent.

    Aggregates historical M2M contract outcomes: success rate, dispute rate,
    and average delivery quality as judged by Arbitrator agents.
    """
    raise NotImplementedError


@router.post("/dispute", response_model=DisputeResponse, status_code=status.HTTP_201_CREATED)
async def file_dispute(
    body: DisputeRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> DisputeResponse:
    """
    File a quality dispute against a contract executor.

    If the Arbitrator agent rules in favor of the claimant, the respondent's
    Trust Score is permanently lowered.
    """
    raise NotImplementedError


@router.get("/dispute/{dispute_id}", response_model=DisputeResponse)
async def get_dispute(
    dispute_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> DisputeResponse:
    raise NotImplementedError
