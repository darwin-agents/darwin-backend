"""
FastAPI dependency injectors shared across all routers.

Two distinct authentication realms:
  - Human JWT: issued by Supabase Auth, validated with SUPABASE_JWT_SECRET
  - Agent JWT: issued by this backend on spawn, validated with AGENT_JWT_SECRET

An agent cannot call a human-only endpoint and vice versa.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import Settings, get_settings
from db.client import get_pool

_bearer = HTTPBearer(auto_error=True)

# ── Settings ──────────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]

# ── Database ──────────────────────────────────────────────────────────────────

PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]

# ── Human Auth (Supabase JWT) ─────────────────────────────────────────────────


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer)],
    settings: SettingsDep,
) -> dict:
    """
    Validate a Supabase-issued JWT.
    Returns the decoded payload containing `sub` (user_id) and `email`.
    """
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return payload


CurrentUser = Annotated[dict, Depends(get_current_user)]

# ── Agent Auth (internal JWT) ─────────────────────────────────────────────────


async def get_current_agent(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer)],
    settings: SettingsDep,
) -> dict:
    """
    Validate a short-lived agent JWT signed with AGENT_JWT_SECRET.
    Returns payload: { darwin_id, session_id, type="agent" }
    """
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.agent_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_agent_token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden"},
        )
    return payload


CurrentAgent = Annotated[dict, Depends(get_current_agent)]


# ── JWT minting helpers ───────────────────────────────────────────────────────


def mint_agent_jwt(darwin_id: str, session_id: str, settings: Settings) -> str:
    """Mint a short-lived JWT for a newly spawned Modal container."""
    now = datetime.now(UTC)
    payload = {
        "darwin_id": darwin_id,
        "session_id": session_id,
        "type": "agent",
        "iat": now,
        "exp": now + timedelta(hours=settings.agent_jwt_expire_hours),
    }
    return jwt.encode(
        payload,
        settings.agent_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def mint_session_link_jwt(
    darwin_id: str,
    browserbase_session_id: str,
    purpose: str,
    ttl_seconds: int,
    settings: Settings,
) -> str:
    """Time-limited JWT for mobile CAPTCHA / 2FA deep-link."""
    now = datetime.now(UTC)
    payload = {
        "darwin_id": darwin_id,
        "browserbase_session_id": browserbase_session_id,
        "purpose": purpose,
        "type": "session_link",
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(
        payload,
        settings.agent_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_session_link_jwt(token: str, settings: Settings) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.agent_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_session_link"},
        ) from exc
    if payload.get("type") != "session_link":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden"},
        )
    return payload
