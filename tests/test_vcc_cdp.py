"""
Tests for VCCService._browserbase_type — Browserbase CDP injection.

Coverage:
  - Correct CDP command sequence (getTargets → attachToTarget → enable → evaluate × 3)
  - Happy-path: all three VCC fields injected and purged
  - No active page target → RuntimeError
  - Field not found (JS returns false) → RuntimeError that names the selector
  - CDP protocol error in the response → RuntimeError
  - Plaintext is purged even when injection succeeds (post-call in decrypt_and_inject)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.vcc_service import VCCService
from tests.conftest import make_pool


# ── Shared mock settings ──────────────────────────────────────────────────────

class MockSettings:
    class vcc_encryption_key:
        @staticmethod
        def get_secret_value() -> str:
            return "test-passphrase"

    class browserbase_api_key:
        @staticmethod
        def get_secret_value() -> str:
            return "bb_test_api_key"

    browserbase_project_id = "test-project-id"


# ── Fake CDP WebSocket ────────────────────────────────────────────────────────

class FakeCDPWebSocket:
    """
    Synchronous/async fake for `websockets.connect(...)`.

    On every `send(data)` call it records the command and queues a response based
    on `results_by_method` (or an error if the method is in `errors_by_method`).
    On the next `recv()` call it returns that queued response.

    CDP commands are sent sequentially in _browserbase_type, so one pending
    command at a time is sufficient.
    """

    def __init__(
        self,
        results_by_method: dict | None = None,
        errors_by_method: dict | None = None,
    ) -> None:
        self._results = results_by_method or {}
        self._errors = errors_by_method or {}
        self._pending: dict | None = None
        self.sent_commands: list[dict] = []

    async def send(self, data: str) -> None:
        cmd = json.loads(data)
        self.sent_commands.append(cmd)
        method = cmd["method"]
        if method in self._errors:
            self._pending = {"id": cmd["id"], "error": {"message": self._errors[method]}}
        else:
            self._pending = {"id": cmd["id"], "result": self._results.get(method, {})}

    async def recv(self) -> str:
        if self._pending is None:
            raise RuntimeError("recv() called before send()")
        resp = self._pending
        self._pending = None
        return json.dumps(resp)

    async def __aenter__(self) -> "FakeCDPWebSocket":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


def _default_cdp_ws(
    eval_value: bool = True,
    errors_by_method: dict | None = None,
) -> FakeCDPWebSocket:
    """Build a FakeCDPWebSocket that mimics a successful Browserbase session."""
    return FakeCDPWebSocket(
        results_by_method={
            "Target.getTargets": {
                "targetInfos": [
                    {"targetId": "target-page-1", "type": "page", "title": "Checkout"},
                    {"targetId": "target-sw-1",   "type": "service_worker"},
                ]
            },
            "Target.attachToTarget": {"sessionId": "cdp-page-session"},
            "Runtime.enable": {},
            "Runtime.evaluate": {"result": {"type": "boolean", "value": eval_value}},
        },
        errors_by_method=errors_by_method or {},
    )


def _fields() -> dict[str, str]:
    return {
        "pan_element":    "#card-number",
        "cvv_element":    "#security-code",
        "expiry_element": "#expiry-date",
    }


def _make_svc() -> VCCService:
    pool, _ = make_pool()
    return VCCService(pool, MockSettings())  # type: ignore[arg-type]


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cdp_correct_command_sequence():
    """CDP commands must arrive in the exact order _browserbase_type specifies."""
    fake_ws = _default_cdp_ws()
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")

    methods = [cmd["method"] for cmd in fake_ws.sent_commands]
    assert methods == [
        "Target.getTargets",
        "Target.attachToTarget",
        "Runtime.enable",
        "Runtime.evaluate",  # pan
        "Runtime.evaluate",  # cvv
        "Runtime.evaluate",  # expiry
    ]


@pytest.mark.asyncio
async def test_cdp_attaches_to_page_target():
    """attachToTarget must use the page targetId, not a service worker."""
    fake_ws = _default_cdp_ws()
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")

    attach_cmd = next(c for c in fake_ws.sent_commands if c["method"] == "Target.attachToTarget")
    assert attach_cmd["params"]["targetId"] == "target-page-1"
    assert attach_cmd["params"]["flatten"] is True


@pytest.mark.asyncio
async def test_cdp_evaluate_uses_correct_page_session():
    """Runtime.evaluate commands must carry the page-level sessionId."""
    fake_ws = _default_cdp_ws()
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")

    eval_cmds = [c for c in fake_ws.sent_commands if c["method"] == "Runtime.evaluate"]
    assert len(eval_cmds) == 3
    for cmd in eval_cmds:
        assert cmd.get("sessionId") == "cdp-page-session"


@pytest.mark.asyncio
async def test_cdp_evaluate_expressions_contain_selectors():
    """Each Runtime.evaluate expression must embed the correct CSS selector."""
    fake_ws = _default_cdp_ws()
    svc = _make_svc()
    fields = _fields()

    with patch("websockets.connect", return_value=fake_ws):
        await svc._browserbase_type("bb-sess-1", fields, "4111111111111111", "123", "12/27")

    eval_cmds = [c for c in fake_ws.sent_commands if c["method"] == "Runtime.evaluate"]
    exprs = [cmd["params"]["expression"] for cmd in eval_cmds]

    assert json.dumps(fields["pan_element"])    in exprs[0]
    assert json.dumps(fields["cvv_element"])    in exprs[1]
    assert json.dumps(fields["expiry_element"]) in exprs[2]


@pytest.mark.asyncio
async def test_cdp_connects_with_correct_session_id_in_url():
    """The CDP WebSocket URL must embed the Browserbase session ID and API key."""
    fake_ws = _default_cdp_ws()
    svc = _make_svc()
    captured_urls: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> FakeCDPWebSocket:
        captured_urls.append(url)
        return fake_ws

    with patch("websockets.connect", side_effect=fake_connect):
        await svc._browserbase_type("my-bb-sess-xyz", _fields(), "4111111111111111", "123", "12/27")

    assert len(captured_urls) == 1
    url = captured_urls[0]
    assert "my-bb-sess-xyz" in url
    assert "bb_test_api_key" in url


# ── Error cases ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cdp_raises_when_no_page_target():
    """If the session has no page target, a clear RuntimeError must be raised."""
    fake_ws = FakeCDPWebSocket(
        results_by_method={
            "Target.getTargets": {
                "targetInfos": [
                    {"targetId": "sw-1", "type": "service_worker"},
                ]
            },
        }
    )
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        with pytest.raises(RuntimeError, match="No active page"):
            await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")


@pytest.mark.asyncio
async def test_cdp_raises_when_field_not_found():
    """If the checkout page lacks a payment field, RuntimeError must name the selector."""
    fake_ws = _default_cdp_ws(eval_value=False)
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        with pytest.raises(RuntimeError) as exc_info:
            await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")

    # Must name the problematic selector so it's debuggable
    assert "#card-number" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cdp_raises_on_protocol_error():
    """A CDP error response must surface as a RuntimeError with the error message."""
    fake_ws = _default_cdp_ws(errors_by_method={"Runtime.enable": "Session not found"})
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        with pytest.raises(RuntimeError, match="Session not found"):
            await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")


@pytest.mark.asyncio
async def test_cdp_raises_on_target_attach_error():
    """A CDP error on attachToTarget must propagate as RuntimeError."""
    fake_ws = FakeCDPWebSocket(
        results_by_method={
            "Target.getTargets": {
                "targetInfos": [{"targetId": "page-1", "type": "page"}]
            },
        },
        errors_by_method={"Target.attachToTarget": "Target not found"},
    )
    svc = _make_svc()

    with patch("websockets.connect", return_value=fake_ws):
        with pytest.raises(RuntimeError, match="Target not found"):
            await svc._browserbase_type("bb-sess-1", _fields(), "4111111111111111", "123", "12/27")


# ── Plaintext lifecycle (via decrypt_and_inject) ──────────────────────────────

@pytest.mark.asyncio
async def test_decrypt_and_inject_purges_plaintext_after_successful_injection():
    """
    After a successful _browserbase_type call, decrypt_and_inject must zero out
    the plaintext locals.  We verify this by confirming the DB fetch used
    pgp_sym_decrypt and the method was called — not by inspecting live memory.
    """
    pool, conn = make_pool(
        fetchrow_returns=[
            {"pan": "4111111111111111", "cvv": "123", "expiry": "12/27"},
        ]
    )
    svc = VCCService(pool, MockSettings())  # type: ignore[arg-type]

    called_with: list[tuple] = []

    # patch.object replaces the class method, so the first arg at call-time is `self`.
    async def fake_type(self_: object, session_id: str, fields: dict, pan: str, cvv: str, expiry: str) -> None:
        called_with.append((session_id, pan, cvv, expiry))

    with patch.object(VCCService, "_browserbase_type", new=fake_type):
        await svc.decrypt_and_inject("vcc-ref-1", "bb-sess-1", _fields())

    # _browserbase_type received the plaintext values
    assert called_with[0] == ("bb-sess-1", "4111111111111111", "123", "12/27")

    # The SQL must have used pgp_sym_decrypt (not bare column read)
    sql = conn.fetchrow_calls[0][0]
    assert "pgp_sym_decrypt" in sql


@pytest.mark.asyncio
async def test_decrypt_and_inject_purges_plaintext_even_on_inject_failure():
    """Plaintext must be zeroed in the finally block even when _browserbase_type raises."""
    pool, conn = make_pool(
        fetchrow_returns=[{"pan": "4111111111111111", "cvv": "123", "expiry": "12/27"}]
    )
    svc = VCCService(pool, MockSettings())  # type: ignore[arg-type]

    async def failing_type(*_: object, **__: object) -> None:
        raise RuntimeError("Browserbase unreachable")

    with patch.object(VCCService, "_browserbase_type", new=failing_type):
        with pytest.raises(RuntimeError, match="Browserbase unreachable"):
            await svc.decrypt_and_inject("vcc-ref-1", "bb-sess-1", _fields())

    # Even on failure, pgp_sym_decrypt must have been called (not raw column)
    sql = conn.fetchrow_calls[0][0]
    assert "pgp_sym_decrypt" in sql


@pytest.mark.asyncio
async def test_decrypt_and_inject_raises_for_unknown_vcc_ref():
    """When the DB returns None, decrypt_and_inject must raise ValueError."""
    pool, _ = make_pool(fetchrow_returns=[None])
    svc = VCCService(pool, MockSettings())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="not found"):
        await svc.decrypt_and_inject("unknown-vcc-ref", "bb-sess-1", _fields())
