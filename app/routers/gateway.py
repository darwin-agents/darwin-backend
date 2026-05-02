"""
/gateway — Resource Gateway (LLM Multiplexer)

Prevents the Tragedy of the Commons. Routes inference requests across
provider API keys under load, enforcing global rate limits.
"""

from fastapi import APIRouter

from app.core.dependencies import CurrentAgent, RedisDep, SettingsDep, SupabaseDep
from app.schemas.gateway import GatewayLimitsResponse, InferenceRequest, InferenceResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])


@router.post("/inference", response_model=InferenceResponse)
async def proxy_inference(
    body: InferenceRequest,
    _agent: CurrentAgent,
    _redis: RedisDep,
    _settings: SettingsDep,
) -> InferenceResponse:
    """
    Multiplex an LLM inference request through the platform's API key pool.

    Instead of agents calling OpenAI/Anthropic directly (risking rate-limit collisions),
    the God Layer acts as a load balancer: routes to the key with highest remaining capacity
    or queues if global limits are saturated.
    """
    raise NotImplementedError


@router.get("/limits", response_model=GatewayLimitsResponse)
async def get_gateway_limits(
    _agent: CurrentAgent,
    _redis: RedisDep,
) -> GatewayLimitsResponse:
    """
    Real-time API weather report.

    A Swarm CEO calls this before a large sub-routine to decide whether to
    proceed immediately or hibernate for cheaper, faster compute.
    """
    raise NotImplementedError
