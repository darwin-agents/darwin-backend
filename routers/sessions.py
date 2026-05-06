"""
/sessions — Live Browserbase WebSocket relay for CAPTCHA solving.
"""

from __future__ import annotations

import asyncio

import websockets  # type: ignore[import-untyped]
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from config import Settings
from deps import decode_session_link_jwt

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.websocket("/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: str,
    token: str,
    settings: Settings = None,  # type: ignore[assignment]
) -> None:
    """
    Bidirectional relay between the human's browser and the Browserbase live-view stream.

    Flow:
    1. Verify the time-limited JWT in the ?token= query param.
    2. Upgrade HTTP → WebSocket.
    3. Open a second WebSocket to Browserbase live-stream.
    4. Forward Browserbase video frames → human browser (binary or text).
    5. Forward human click/touch events → Browserbase.
    6. On "resume_agent" signal from the human: wake the Darwin, send confirmation, close.
    """
    _settings: Settings = settings or __import__("config").get_settings()

    try:
        payload = decode_session_link_jwt(token, _settings)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if payload.get("browserbase_session_id") != session_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    bb_api_key = _settings.browserbase_api_key.get_secret_value()
    bb_project_id = _settings.browserbase_project_id
    bb_ws_url = (
        f"wss://connect.browserbase.com/ws/{session_id}"
        f"?apiKey={bb_api_key}&projectId={bb_project_id}"
    )

    try:
        async with websockets.connect(bb_ws_url, max_size=10 * 1024 * 1024) as bb_ws:
            await websocket.send_text("stream_connected")

            async def relay_bb_to_client() -> None:
                """Forward Browserbase frames (video, JSON events) to the human browser."""
                try:
                    async for msg in bb_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except (websockets.ConnectionClosed, WebSocketDisconnect):
                    pass

            async def relay_client_to_bb() -> None:
                """Forward human input events to Browserbase; intercept the resume signal."""
                try:
                    while True:
                        raw = await websocket.receive()
                        if raw["type"] == "websocket.disconnect":
                            return

                        data: str | bytes | None = raw.get("text") or raw.get("bytes")
                        if data is None:
                            continue

                        if data == "resume_agent":
                            await _wake_agent(payload)
                            await websocket.send_text("agent_resumed")
                            return

                        await bb_ws.send(data)
                except (WebSocketDisconnect, websockets.ConnectionClosed):
                    pass

            tasks = [
                asyncio.create_task(relay_bb_to_client()),
                asyncio.create_task(relay_client_to_bb()),
            ]
            _done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    finally:
        await websocket.close()


async def _wake_agent(payload: dict) -> None:
    """Trigger an agent wake via Celery after a successful CAPTCHA hand-off."""
    from db.client import get_pool
    from workers.wake_worker import _execute_wake

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT wake_prompt FROM hibernate_schedule"
            " WHERE darwin_id = $1 AND fired_at IS NULL"
            " ORDER BY created_at DESC LIMIT 1",
            payload["darwin_id"],
        )
    wake_prompt = row["wake_prompt"] if row else "Resume from captcha delegation."
    asyncio.create_task(_execute_wake(payload["darwin_id"], wake_prompt, "captcha_solved"))
