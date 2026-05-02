"""FastAPI dependency injectors shared across all routers."""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.core.security import decode_token
from app.database import get_pg_pool, get_supabase

_bearer = HTTPBearer(auto_error=True)

# ── Settings ─────────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]

# ── Database ─────────────────────────────────────────────────────────────────

SupabaseDep = Annotated[object, Depends(get_supabase)]
PgPoolDep = Annotated[object, Depends(get_pg_pool)]

# ── Redis ─────────────────────────────────────────────────────────────────────

_redis_pool: aioredis.Redis | None = None  # type: ignore[type-arg]


async def get_redis(settings: SettingsDep) -> aioredis.Redis:  # type: ignore[type-arg]
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
    return _redis_pool


RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]  # type: ignore[type-arg]

# ── Auth ─────────────────────────────────────────────────────────────────────


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return payload


async def get_current_agent(
    payload: Annotated[dict, Depends(get_current_user)],
) -> str:
    """Validates that the caller is a Modal container (scope=agent)."""
    if payload.get("scope") != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Endpoint restricted to agent tokens.",
        )
    agent_id: str = payload["sub"]
    return agent_id


CurrentUser = Annotated[dict, Depends(get_current_user)]
CurrentAgent = Annotated[str, Depends(get_current_agent)]
