"""
Tests for /sessions/{session_id}/stream — live Browserbase WebSocket relay.

Coverage:
  - Invalid or expired JWT → connection rejected (WS 1008)
  - JWT session_id mismatch → connection rejected (WS 1008)
  - Wrong token type (agent JWT) → connection rejected
  - stream_connected confirmation sent on successful relay start
  - Text frames from Browserbase forwarded to client
  - Binary (video) frames from Browserbase forwarded to client
  - Multiple frames forwarded in order
  - "resume_agent" signal: triggers _wake_agent, sends "agent_resumed", closes relay
  - _wake_agent: fetches wake_prompt from DB, calls execute_agent_wake.delay
  - _wake_agent: falls back to default prompt when no pending schedule row exists
"""

from __future__ import annotations

# Mock supabase before any app-module imports so db.client can be loaded
# without the optional supabase package installed in the dev environment.
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("supabase", MagicMock())

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from jose import jwt
from starlette.testclient import TestClient

# ── Constants ─────────────────────────────────────────────────────────────────

_AGENT_JWT_SECRET = "test-agent-jwt-secret-min-32chars"
_SESSION_ID       = "bb-session-abc123"
_DARWIN_ID        = "darwin-agent-uuid"

# ── JWT helpers ───────────────────────────────────────────────────────────────

def _session_link_token(
    bb_session_id: str = _SESSION_ID,
    darwin_id: str = _DARWIN_ID,
    ttl_seconds: int = 3600,
    token_type: str = "session_link",
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "darwin_id": darwin_id,
            "browserbase_session_id": bb_session_id,
            "purpose": "captcha",
            "type": token_type,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        },
        _AGENT_JWT_SECRET,
        algorithm="HS256",
    )


def _ws_url(session_id: str = _SESSION_ID, token: str | None = None) -> str:
    tok = token or _session_link_token(session_id)
    return f"/api/v1/sessions/{session_id}/stream?token={tok}"


# ── Minimal FastAPI app containing only the sessions router ───────────────────

@pytest.fixture(scope="module")
def sessions_app() -> FastAPI:
    """
    A standalone FastAPI app with just the sessions router mounted.
    Uses a patched Settings so the JWT secret is deterministic.
    Avoids importing the full main.py (which needs many optional packages).
    """
    from routers.sessions import router

    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1")
    return _app


@pytest.fixture(scope="module")
def fake_settings() -> MagicMock:
    s = MagicMock()
    s.agent_jwt_secret.get_secret_value.return_value = _AGENT_JWT_SECRET
    s.browserbase_api_key.get_secret_value.return_value = "bb_test_key"
    s.browserbase_project_id = "test-proj"
    return s


@pytest.fixture(scope="module")
def sessions_client(sessions_app, fake_settings) -> TestClient:
    # sessions.py resolves settings via __import__("config").get_settings(), so we
    # must patch at the config module level rather than inside routers.sessions.
    with patch("config.get_settings", return_value=fake_settings):
        yield TestClient(sessions_app, raise_server_exceptions=False)


# ── Fake Browserbase WebSockets ──────────────────────────────────────────────

class FakeBBWebSocket:
    """
    Simulates the Browserbase live-view WebSocket from the relay's perspective.
    Frames are yielded one-by-one; once exhausted the async iterator ends,
    which causes relay_bb_to_client to return and asyncio.wait to complete.
    """

    def __init__(self, frames: list | None = None) -> None:
        self._frames = list(frames or [])
        self.sent: list = []

    def __aiter__(self) -> "FakeBBWebSocket":
        self._iter = iter(self._frames)
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def __aenter__(self) -> "FakeBBWebSocket":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


class BlockingBBWebSocket:
    """
    Simulates a live Browserbase session that never sends frames on its own.
    relay_bb_to_client blocks on `await asyncio.sleep(...)` until it is cancelled
    by asyncio.wait after relay_client_to_bb returns.  Use this when the test
    controls the relay via a client-side signal (e.g. 'resume_agent') rather than
    exhausting the Browserbase frame stream.
    """

    def __init__(self) -> None:
        self.sent: list = []

    def __aiter__(self) -> "BlockingBBWebSocket":
        return self

    async def __anext__(self) -> str | bytes:
        import asyncio
        await asyncio.sleep(3600)   # blocks until CancelledError from task.cancel()
        raise StopAsyncIteration

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def __aenter__(self) -> "BlockingBBWebSocket":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


# ── Authentication tests ──────────────────────────────────────────────────────

def test_stream_rejects_invalid_jwt(sessions_client):
    """A garbage token must be rejected before the relay is opened."""
    with pytest.raises(Exception):
        with sessions_client.websocket_connect(_ws_url(token="not.a.valid.jwt")) as ws:
            ws.receive_text()


def test_stream_rejects_expired_jwt(sessions_client):
    """An expired session-link JWT must be rejected."""
    expired = _session_link_token(ttl_seconds=-1)
    with pytest.raises(Exception):
        with sessions_client.websocket_connect(_ws_url(token=expired)) as ws:
            ws.receive_text()


def test_stream_rejects_session_id_mismatch(sessions_client):
    """A JWT minted for session A must not grant access to session B's stream."""
    token_for_other = _session_link_token(bb_session_id="other-session-id")
    with pytest.raises(Exception):
        with sessions_client.websocket_connect(
            _ws_url(session_id=_SESSION_ID, token=token_for_other)
        ) as ws:
            ws.receive_text()


def test_stream_rejects_wrong_jwt_type(sessions_client):
    """An agent JWT (type='agent') must not pass as a session-link token."""
    agent_token = _session_link_token(token_type="agent")
    with pytest.raises(Exception):
        with sessions_client.websocket_connect(_ws_url(token=agent_token)) as ws:
            ws.receive_text()


# ── Relay behaviour tests ─────────────────────────────────────────────────────

def test_stream_sends_stream_connected_on_open(sessions_client):
    """First message after successful auth must be 'stream_connected'."""
    fake_bb = FakeBBWebSocket(frames=[])

    with patch("routers.sessions.websockets.connect", return_value=fake_bb):
        with sessions_client.websocket_connect(_ws_url()) as ws:
            msg = ws.receive_text()

    assert msg == "stream_connected"


def test_stream_forwards_text_frame_from_browserbase(sessions_client):
    """A text JSON event from Browserbase must be forwarded verbatim to the client."""
    event = '{"type":"captcha_detected","url":"https://example.com"}'
    fake_bb = FakeBBWebSocket(frames=[event])

    with patch("routers.sessions.websockets.connect", return_value=fake_bb):
        with sessions_client.websocket_connect(_ws_url()) as ws:
            ws.receive_text()           # stream_connected
            forwarded = ws.receive_text()

    assert forwarded == event


def test_stream_forwards_binary_frame_from_browserbase(sessions_client):
    """Binary video frames from Browserbase must be forwarded as bytes."""
    video_chunk = b"\x00\x01\x02\x03video_frame_data"
    fake_bb = FakeBBWebSocket(frames=[video_chunk])

    with patch("routers.sessions.websockets.connect", return_value=fake_bb):
        with sessions_client.websocket_connect(_ws_url()) as ws:
            ws.receive_text()           # stream_connected
            forwarded = ws.receive_bytes()

    assert forwarded == video_chunk


def test_stream_forwards_multiple_frames_in_order(sessions_client):
    """Multiple Browserbase frames must arrive at the client in the same order."""
    frames = [f"frame-{i}" for i in range(5)]
    fake_bb = FakeBBWebSocket(frames=frames)

    with patch("routers.sessions.websockets.connect", return_value=fake_bb):
        with sessions_client.websocket_connect(_ws_url()) as ws:
            ws.receive_text()   # stream_connected
            received = [ws.receive_text() for _ in frames]

    assert received == frames


def test_stream_resume_agent_triggers_wake_and_confirms(sessions_client):
    """
    'resume_agent' must call _wake_agent, reply 'agent_resumed', and close.

    BlockingBBWebSocket is required here: with an empty FakeBBWebSocket the
    relay_bb_to_client task would finish immediately (StopAsyncIteration),
    asyncio.wait would complete, relay_client_to_bb would be cancelled, and
    the WebSocket would close before the test client could send 'resume_agent'.
    The blocking variant keeps relay_bb_to_client alive (it sleeps until
    task.cancel() fires CancelledError through the asyncio.sleep call) so
    relay_client_to_bb gets a chance to process the signal first.
    """
    blocking_bb = BlockingBBWebSocket()

    with patch("routers.sessions.websockets.connect", return_value=blocking_bb), \
         patch("routers.sessions._wake_agent", new_callable=AsyncMock) as mock_wake:

        with sessions_client.websocket_connect(_ws_url()) as ws:
            ws.receive_text()           # stream_connected
            ws.send_text("resume_agent")
            confirmation = ws.receive_text()

    assert confirmation == "agent_resumed"
    mock_wake.assert_awaited_once()
    payload_arg = mock_wake.call_args[0][0]
    assert payload_arg["darwin_id"] == _DARWIN_ID


# ── _wake_agent unit tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wake_agent_calls_execute_with_db_prompt():
    """_wake_agent fetches the pending wake_prompt from DB and calls execute_agent_wake.delay."""
    from routers.sessions import _wake_agent

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__  = AsyncMock(return_value=False)
    mock_conn.fetchrow   = AsyncMock(return_value={"wake_prompt": "Solve the checkout CAPTCHA."})
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    # _wake_agent does local imports: `from db.client import get_pool` and
    # `from workers.wake_worker import execute_agent_wake`.  Patch at source.
    with patch("db.client.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("workers.wake_worker.execute_agent_wake") as mock_task:

        await _wake_agent({"darwin_id": "agent-42"})

    mock_task.delay.assert_called_once_with(
        "agent-42", "Solve the checkout CAPTCHA.", "captcha_solved"
    )


@pytest.mark.asyncio
async def test_wake_agent_uses_fallback_prompt_when_no_schedule_row():
    """When hibernate_schedule has no pending row, the fallback prompt is used."""
    from routers.sessions import _wake_agent

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__  = AsyncMock(return_value=False)
    mock_conn.fetchrow   = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    with patch("db.client.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("workers.wake_worker.execute_agent_wake") as mock_task:

        await _wake_agent({"darwin_id": "agent-99"})

    _darwin_id, wake_prompt, trigger = mock_task.delay.call_args[0]
    assert wake_prompt == "Resume from captcha delegation."
    assert trigger == "captcha_solved"


# ── decode_session_link_jwt unit tests ────────────────────────────────────────

def test_decode_session_link_jwt_valid():
    """A correctly signed session_link token must decode to its payload."""
    from config import get_settings
    from deps import decode_session_link_jwt

    token = _session_link_token()
    with patch("deps.get_settings") as mock_gs:
        mock_gs.return_value.agent_jwt_secret.get_secret_value.return_value = _AGENT_JWT_SECRET
        payload = decode_session_link_jwt(token, mock_gs.return_value)

    assert payload["darwin_id"] == _DARWIN_ID
    assert payload["browserbase_session_id"] == _SESSION_ID
    assert payload["type"] == "session_link"


def test_decode_session_link_jwt_rejects_garbage():
    """A garbage string must raise (HTTPException wrapping JWTError)."""
    from fastapi import HTTPException
    from deps import decode_session_link_jwt

    fake_settings = MagicMock()
    fake_settings.agent_jwt_secret.get_secret_value.return_value = _AGENT_JWT_SECRET

    with pytest.raises(HTTPException):
        decode_session_link_jwt("garbage.token.here", fake_settings)


def test_decode_session_link_jwt_rejects_wrong_type():
    """An otherwise valid JWT with type='agent' must be rejected."""
    from fastapi import HTTPException
    from deps import decode_session_link_jwt

    wrong_type_token = _session_link_token(token_type="agent")
    fake_settings = MagicMock()
    fake_settings.agent_jwt_secret.get_secret_value.return_value = _AGENT_JWT_SECRET

    with pytest.raises(HTTPException):
        decode_session_link_jwt(wrong_type_token, fake_settings)
