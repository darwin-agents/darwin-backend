"""
Shared fixtures for the darwin-backend test suite.

Key design:
  - app.dependency_overrides replaces get_pool with a mock so no real DB is ever touched.
  - Properly signed JWT helpers for both auth realms.
  - MockConn / MockPool fully implement the asyncpg interface (transaction ctx manager, etc).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt

# ── Environment vars — set before any app module imports ─────────────────────
_SUPABASE_JWT_SECRET = "test-supabase-jwt-secret-32chars!!"
_AGENT_JWT_SECRET    = "test-agent-jwt-secret-min-32chars"

os.environ.update(
    SUPABASE_URL="https://test.supabase.co",
    SUPABASE_ANON_KEY="test-anon-key",
    SUPABASE_SERVICE_ROLE_KEY="test-service-role-key",
    SUPABASE_JWT_SECRET=_SUPABASE_JWT_SECRET,
    DATABASE_URL="postgresql://test:test@localhost/test",
    AGENT_JWT_SECRET=_AGENT_JWT_SECRET,
    VCC_ENCRYPTION_KEY="test-vcc-encryption-passphrase!!",
    MODAL_TOKEN_ID="ak-test",
    MODAL_TOKEN_SECRET="as-test",
    TELEGRAM_BOT_TOKEN="123456:testbottoken",
    STRIPE_WEBHOOK_SECRET="whsec_test",
    OPENAI_API_KEY="sk-test",
    ANTHROPIC_API_KEY_1="sk-ant-test",
)

# ── JWT helpers ───────────────────────────────────────────────────────────────

def make_human_jwt(user_id: str = "user-test-uuid", email: str = "test@darwin.ai") -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": "authenticated",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return jwt.encode(payload, _SUPABASE_JWT_SECRET, algorithm="HS256")


def make_agent_jwt(
    darwin_id: str = "agent-test-uuid",
    session_id: str = "session-test-uuid",
    expired: bool = False,
) -> str:
    exp = datetime.now(UTC) + (timedelta(seconds=-1) if expired else timedelta(hours=2))
    payload = {
        "darwin_id": darwin_id,
        "session_id": session_id,
        "type": "agent",
        "iat": datetime.now(UTC),
        "exp": exp,
    }
    return jwt.encode(payload, _AGENT_JWT_SECRET, algorithm="HS256")


def human_headers(user_id: str = "user-test-uuid") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_human_jwt(user_id)}"}


def agent_headers(
    darwin_id: str = "agent-test-uuid",
    session_id: str = "session-test-uuid",
) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_agent_jwt(darwin_id, session_id)}"}


# ── asyncpg mock helpers ──────────────────────────────────────────────────────

class MockConn:
    """
    Behaves like an asyncpg connection:
      - async context manager (pool.acquire())
      - transaction() returns itself as async context manager
      - fetchrow(), fetch(), execute() are async
    """

    def __init__(
        self,
        fetchrow_returns: list[Any] | None = None,
        fetch_returns: list[Any] | None = None,
    ) -> None:
        self._fetchrow_returns = list(fetchrow_returns or [])
        self._fetch_returns = list(fetch_returns or [])
        self._fr_idx = 0
        self._f_idx = 0
        self.execute_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []

    async def __aenter__(self) -> "MockConn":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    def transaction(self) -> "MockConn":
        return self

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.fetchrow_calls.append((query, args))
        if self._fr_idx < len(self._fetchrow_returns):
            val = self._fetchrow_returns[self._fr_idx]
            self._fr_idx += 1
            return val
        return None

    async def fetch(self, query: str, *args: Any) -> list:
        if self._f_idx < len(self._fetch_returns):
            val = self._fetch_returns[self._f_idx]
            self._f_idx += 1
            return val
        return []

    async def execute(self, query: str, *args: Any) -> None:
        self.execute_calls.append((query, args))


class MockPool:
    def __init__(self, conn: MockConn) -> None:
        self._conn = conn

    def acquire(self) -> MockConn:
        return self._conn

    async def close(self) -> None:
        pass


def make_pool(
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
) -> tuple[MockPool, MockConn]:
    conn = MockConn(fetchrow_returns=fetchrow_returns, fetch_returns=fetch_returns)
    return MockPool(conn), conn


# ── FastAPI app fixture with dependency overrides ─────────────────────────────

@pytest.fixture(scope="session")
def app():
    """
    Return the FastAPI app with get_pool and get_supabase overridden.

    Using app.dependency_overrides is the only reliable way to prevent FastAPI
    from resolving real DB dependencies before route functions run.
    """
    from main import app as _app
    from db.client import get_pool, get_supabase

    _default_pool, _ = make_pool()

    async def _mock_pool():
        return _default_pool

    async def _mock_supabase():
        return AsyncMock()

    _app.dependency_overrides[get_pool] = _mock_pool
    _app.dependency_overrides[get_supabase] = _mock_supabase

    yield _app

    _app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── Per-test pool factory ─────────────────────────────────────────────────────

@pytest.fixture
def mock_pool_factory():
    return make_pool
