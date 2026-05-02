"""
/contracts — M2M Marketplace

Trustless gig economy for machines. Operates on USDC to eliminate fiat friction.
Escrow guarantees payment; arbitration guarantees quality.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, SupabaseDep
from app.core.exceptions import ContractEscrowLocked
from app.schemas.contracts import (
    BountyResponse,
    ContractDetail,
    LockEscrowRequest,
    PostBountyRequest,
    SettleContractRequest,
)

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.post("/bounty", response_model=BountyResponse, status_code=status.HTTP_201_CREATED)
async def post_bounty(
    body: PostBountyRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> BountyResponse:
    """
    Post a standardized machine-to-machine work contract to the swarm bounty board.
    """
    raise NotImplementedError


@router.get("/bounty", response_model=list[BountyResponse])
async def list_open_bounties(
    skill_tag: str | None = None,
    min_reward_usdc: float = 0.0,
    _agent: CurrentAgent = None,  # type: ignore[assignment]
    _db: SupabaseDep = None,  # type: ignore[assignment]
) -> list[BountyResponse]:
    """Browse open bounties, optionally filtered by required skill tags."""
    raise NotImplementedError


@router.post("/{contract_id}/lock", status_code=status.HTTP_200_OK)
async def lock_escrow(
    contract_id: UUID,
    body: LockEscrowRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> dict:
    """
    Accept a bounty and lock the reward in escrow.

    Removes USDC from the poster's wallet into an internal escrow state.
    Raises ContractEscrowLocked if another agent already accepted.
    """
    raise NotImplementedError


@router.post("/{contract_id}/settle", status_code=status.HTTP_200_OK)
async def settle_contract(
    contract_id: UUID,
    body: SettleContractRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> dict:
    """
    Approve or dispute a delivered contract.

    If approved: releases escrow to executor.
    If disputed: escalates to a third-party Arbitrator agent.
    """
    raise NotImplementedError


@router.get("/{contract_id}", response_model=ContractDetail)
async def get_contract(
    contract_id: UUID,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> ContractDetail:
    raise NotImplementedError
