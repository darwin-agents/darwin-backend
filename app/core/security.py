"""JWT signing, verification, and password hashing utilities."""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(
    subject: str,
    extra: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    cfg = get_settings()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=cfg.access_token_expire_minutes)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire, **(extra or {})}
    return jwt.encode(payload, cfg.secret_key.get_secret_value(), algorithm=cfg.algorithm)


def create_agent_token(agent_id: str) -> str:
    """Short-lived JWT issued to a Modal container for god-layer comms."""
    cfg = get_settings()
    return create_agent_token_with_cfg(agent_id, cfg)


def create_agent_token_with_cfg(agent_id: str, cfg: Any) -> str:
    from datetime import timedelta

    return create_access_token(
        subject=agent_id,
        extra={"scope": "agent"},
        expires_delta=timedelta(minutes=cfg.agent_token_expire_minutes),
    )


def decode_token(token: str) -> dict[str, Any]:
    cfg = get_settings()
    try:
        return jwt.decode(token, cfg.secret_key.get_secret_value(), algorithms=[cfg.algorithm])
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
