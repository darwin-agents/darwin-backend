"""
/darwins — Agent Lifecycle Manager

Handles spawning, state transitions, hibernation, and termination of Darwin Agents.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentUser, RedisDep, SettingsDep, SupabaseDep
from app.core.exceptions import AgentFSMConflict, AgentNotFound
from app.schemas.darwins import (
    AgentListResponse,
    AgentState,
    AgentStateResponse,
    HibernateRequest,
    SpawnAgentRequest,
    SpawnAgentResponse,
    TerminateRequest,
)

router = APIRouter(prefix="/darwins", tags=["darwins"])

# Valid FSM transitions: current_state → allowed_next_states
_FSM: dict[AgentState, set[AgentState]] = {
    AgentState.PROVISIONING: {AgentState.ACTIVE, AgentState.TERMINATED},
    AgentState.ACTIVE: {AgentState.HIBERNATING, AgentState.TERMINATED},
    AgentState.HIBERNATING: {AgentState.ACTIVE, AgentState.TERMINATED},
    AgentState.TERMINATED: set(),
}


@router.post("/spawn", response_model=SpawnAgentResponse, status_code=status.HTTP_201_CREATED)
async def spawn_agent(
    body: SpawnAgentRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
    _settings: SettingsDep,
) -> SpawnAgentResponse:
    """
    Initialize a new Darwin Agent.

    1. Runs semantic intent scan (Auditor model) on the directive.
    2. Creates the agent record in Supabase.
    3. Allocates initial fiat budget in the ledger.
    4. Provisions the Modal container + attaches a fresh SQLite Volume.
    5. Returns the agent's opaque VCC reference token.
    """
    # TODO: integrate ModalService.spawn_container() and EncryptionService.mint_vcc()
    raise NotImplementedError


@router.get("/{agent_id}/state", response_model=AgentStateResponse)
async def get_agent_state(
    agent_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> AgentStateResponse:
    """Return current FSM state and real-time metabolic metrics."""
    # TODO: query Supabase agents table
    raise NotImplementedError


@router.get("/", response_model=AgentListResponse)
async def list_agents(
    owner_id: str,
    state: AgentState | None = None,
    _user: CurrentUser = None,  # type: ignore[assignment]
    _db: SupabaseDep = None,  # type: ignore[assignment]
) -> AgentListResponse:
    """List all agents belonging to an owner, optionally filtered by state."""
    raise NotImplementedError


@router.post("/{agent_id}/hibernate", status_code=status.HTTP_202_ACCEPTED)
async def hibernate_agent(
    agent_id: UUID,
    body: HibernateRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
    _redis: RedisDep,
) -> dict:
    """
    Transition agent to HIBERNATING state.

    Saves episodic memory snapshot, schedules the wake-up trigger (cron or webhook),
    and releases the Modal container.
    """
    raise NotImplementedError


@router.post("/{agent_id}/terminate", status_code=status.HTTP_200_OK)
async def terminate_agent(
    agent_id: UUID,
    body: TerminateRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> dict:
    """
    Kill Switch — incinerate the Modal container, freeze the VCC, and archive the agent.

    This is the most critical endpoint. It acts instantly and ruthlessly.
    """
    raise NotImplementedError


@router.post("/{agent_id}/wake", status_code=status.HTTP_202_ACCEPTED)
async def wake_agent(
    agent_id: UUID,
    wake_prompt: str,
    _user: CurrentUser,
    _db: SupabaseDep,
    _settings: SettingsDep,
) -> dict:
    """
    Resurrect a hibernating agent by spinning up a fresh Modal container
    and injecting the wake-up prompt with full episodic context.
    """
    raise NotImplementedError
