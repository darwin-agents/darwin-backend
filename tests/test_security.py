"""Tests for JWT security utilities."""

from datetime import timedelta

import pytest

from app.core.security import create_access_token, decode_token


def test_create_and_decode_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "testsecret" * 4)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "key")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwtsecret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("MODAL_TOKEN_ID", "id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("VCC_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

    from app.config import get_settings
    get_settings.cache_clear()

    token = create_access_token("user-123", expires_delta=timedelta(minutes=5))
    payload = decode_token(token)
    assert payload["sub"] == "user-123"


def test_expired_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "testsecret" * 4)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "key")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwtsecret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("MODAL_TOKEN_ID", "id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("VCC_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

    from app.config import get_settings
    get_settings.cache_clear()

    token = create_access_token("user-123", expires_delta=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="Invalid or expired token"):
        decode_token(token)
