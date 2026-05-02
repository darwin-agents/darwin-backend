"""
/sessions — Live Browserbase WebSocket relay for CAPTCHA solving.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from config import Settings
from deps import SettingsDep, decode_session_link_jwt

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.websocket("/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: str,
    token: str,
    settings: Settings = None,  # type: ignore[assignment]
) -> None:
    """
    Relay live Browserbase session video feed to the human's mobile browser.

    Flow:
    1. Verify the time-limited JWT in the ?token= query param.
    2. Upgrade HTTP → WebSocket.
    3. Open bidirectional relay to Browserbase live-stream.
    4. Forward: Browserbase video frames → human browser.
    5. Forward: human click/touch events → Browserbase.
    6. On "Resume Agent" signal: call /notifications/resolve internally and close relay.
    """
    from fastapi import Depends

    _settings = settings or __import__("config").get_settings()

    # Verify the session-link JWT
    try:
        payload = decode_session_link_jwt(token, _settings)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Ensure the JWT authorises this specific Browserbase session
    if payload.get("browserbase_session_id") != session_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    bb_api_key = _settings.browserbase_api_key.get_secret_value()
    bb_project_id = _settings.browserbase_project_id
    bb_ws_url = f"wss://connect.browserbase.com/ws/{session_id}?apiKey={bb_api_key}&projectId={bb_project_id}"

    try:
        async with httpx.AsyncClient() as client:
            # TODO: httpx does not natively support WebSocket relay.
            # Use websockets library or starlette WebSocket proxy here.
            # Placeholder: echo back a message to confirm connection.
            await websocket.send_text("stream_connected")

            while True:
                data = await websocket.receive_text()
                if data == "resume_agent":
                    from db.client import get_pool
                    from workers.wake_worker import execute_agent_wake
                    pool = await get_pool()
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow(
                            "SELECT wake_prompt FROM hibernate_schedule WHERE darwin_id = $1 AND fired_at IS NULL ORDER BY created_at DESC LIMIT 1",
                            payload["darwin_id"],
                        )
                    wake_prompt = (row["wake_prompt"] if row else "Resume from captcha delegation.")
                    execute_agent_wake.delay(payload["darwin_id"], wake_prompt, "captcha_solved")
                    await websocket.send_text("agent_resumed")
                    break

    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()
