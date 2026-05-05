"""
FastAPI dependency injectors shared across all routers.

Two distinct authentication realms:
  - Human JWT: issued by Supabase Auth — HS256 via SUPABASE_JWT_SECRET, or
    asymmetric (RS*/ES*) via project JWKS (`/auth/v1/.well-known/jwks.json`)
  - Agent JWT: issued by this backend on spawn, validated with AGENT_JWT_SECRET

An agent cannot call a human-only endpoint and vice versa.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import asyncpg
import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt

from config import Settings, get_settings
from db.client import get_pool

_bearer = HTTPBearer(auto_error=True)

# ── Settings ──────────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]

# ── Database ──────────────────────────────────────────────────────────────────

PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]

# ── Human Auth (Supabase JWT) ─────────────────────────────────────────────────

_JWKS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_JWKS_TTL_SEC = 600.0


def _jwks_url(settings: Settings) -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


async def _fetch_jwks(settings: Settings) -> list[dict[str, Any]]:
    url = _jwks_url(settings)
    now = time.monotonic()
    cached = _JWKS_CACHE.get(url)
    if cached is not None and cached[0] > now:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        body = response.json()
    keys = body.get("keys") or []
    _JWKS_CACHE[url] = (now + _JWKS_TTL_SEC, keys)
    return keys


def _pick_jwk(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any] | None:
    if not keys:
        return None
    if kid:
        for entry in keys:
            if entry.get("kid") == kid:
                return entry
    return keys[0]


def _decode_supabase_hs256(token: str, settings: Settings) -> dict:
    return jwt.decode(
        token,
        settings.supabase_jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        options={"verify_aud": False},
    )


def _decode_supabase_asymmetric(token: str, alg: str, jwk_dict: dict[str, Any]) -> dict:
    # JWKS entries may omit ``alg``; jwk.construct needs a concrete algorithm.
    pub = jwk.construct(jwk_dict, algorithm=jwk_dict.get("alg") or alg)
    return jwt.decode(token, pub, algorithms=[alg], options={"verify_aud": False})


async def decode_supabase_user_jwt(token: str, settings: Settings) -> dict:
    """
    Validate a Supabase Auth access token.

    - HS256: verified with ``SUPABASE_JWT_SECRET`` (legacy / symmetric projects).
    - RS*/ES*: verified with keys from the project JWKS endpoint (asymmetric signing keys).
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise JWTError("malformed token header") from exc

    alg = header.get("alg") or "HS256"
    if alg == "HS256":
        return _decode_supabase_hs256(token, settings)

    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512"):
        raise JWTError(f"unsupported jwt alg: {alg}")

    keys = await _fetch_jwks(settings)
    jwk_dict = _pick_jwk(keys, header.get("kid"))
    if not jwk_dict:
        raise JWTError("no jwks keys available for verification")

    return _decode_supabase_asymmetric(token, alg, jwk_dict)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer)],
    settings: SettingsDep,
) -> dict:
    """
    Validate a Supabase-issued JWT.
    Returns the decoded payload containing `sub` (user_id) and `email`.
    """
    try:
        payload = await decode_supabase_user_jwt(credentials.credentials, settings)
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
