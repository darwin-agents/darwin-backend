"""
/swarms — Swarm Orchestrator

Groups multiple Darwin Agents into hierarchical Digital Corporations.
CEO agents can autonomously spawn/terminate subordinates and route treasury funds.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, SupabaseDep
from app.schemas.swarms import (
    AllocateTreasuryRequest,
    CreateSwarmRequest,
    SwarmMembersResponse,
    SwarmResponse,
)

router = APIRouter(prefix="/swarms", tags=["swarms"])


@router.post("/create", response_model=SwarmResponse, status_code=status.HTTP_201_CREATED)
async def create_swarm(
    body: CreateSwarmRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> SwarmResponse:
    """
    Create a Swarm and assign a CEO agent.

    The CEO agent is granted elevated permissions to call POST /darwins/spawn
    and POST /darwins/{id}/terminate autonomously.
    """
    raise NotImplementedError


@router.get("/{swarm_id}", response_model=SwarmResponse)
async def get_swarm(
    swarm_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> SwarmResponse:
    raise NotImplementedError


@router.post("/{swarm_id}/allocate", status_code=status.HTTP_200_OK)
async def allocate_treasury(
    swarm_id: UUID,
    body: AllocateTreasuryRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> dict:
    """
    CEO agent dynamically routes USDC/fiat from the shared treasury
    to a subordinate agent based on its current task.
    """
    raise NotImplementedError


@router.get("/{swarm_id}/members", response_model=SwarmMembersResponse)
async def list_swarm_members(
    swarm_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> SwarmMembersResponse:
    raise NotImplementedError
