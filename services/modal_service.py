"""
ModalService — the only place in the codebase that touches the Modal SDK.

Handles container spawning, kill signals (via Redis), and heartbeat polling.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from jose import jwt

from config import Settings

_KILL_TTL = 300       # seconds the kill signal stays in Redis
_HEARTBEAT_TTL = 30   # agent writes heartbeat every 15s; stale after 30s


class ModalService:
    def __init__(self, settings: Settings, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._settings = settings
        self._redis = redis

    async def spawn_container(
        self,
        darwin_id: str,
        session_id: str,
        wake_prompt: str,
        agent_jwt: str,
        image_tag: str = "darwin-agent:latest",
    ) -> str:
        """
        Provisions a fresh Modal micro-VM.
        Returns the Modal call_id for tracking.
        """
        # Ensure Modal SDK auth is available in-process (local dev often lacks
        # `modal token new` state but we already have secrets in backend env).
        os.environ["MODAL_TOKEN_ID"] = self._settings.modal_token_id.get_secret_value()
        os.environ["MODAL_TOKEN_SECRET"] = self._settings.modal_token_secret.get_secret_value()

        import modal

        # modal>=0.64 uses Function.from_name; lookup() is no longer available.
        fn = modal.Function.from_name(
            self._settings.modal_app_name,
            self._settings.modal_function_name,
            environment_name=self._settings.modal_environment_name,
        )
        # Use aio spawn from async FastAPI handlers (avoids blocking Modal client).
        call = await fn.spawn.aio(
            darwin_id=darwin_id,
            session_id=session_id,
            wake_prompt=wake_prompt,
            agent_jwt=agent_jwt,
            backend_url=self._settings.app_base_url,
        )
        return call.object_id

    async def kill_container(self, darwin_id: str) -> None:
        """
        Signal the agent to exit by writing a kill key to Redis.
        The agent's heartbeat loop checks this key and raises SystemExit.
        Modal does not expose a direct kill API for spawned calls.
        """
        await self._redis.set(f"kill:{darwin_id}", "1", ex=_KILL_TTL)

    async def get_container_status(self, darwin_id: str) -> str:
        """Returns 'running' | 'stopped'."""
        last_beat = await self._redis.get(f"heartbeat:{darwin_id}")
        if not last_beat:
            return "stopped"
        age = time.time() - float(last_beat)
        return "running" if age < _HEARTBEAT_TTL else "stopped"

    async def publish_uplink(self, darwin_id: str, message: str, attachments: list) -> None:
        """Publish a mid-lifecycle prompt via Redis pub/sub channel."""
        import json
        channel = f"uplink:{darwin_id}"
        payload = json.dumps({"message": message, "attachments": attachments})
        await self._redis.publish(channel, payload)

    async def update_image_tag(self, darwin_id: str, new_tag: str) -> None:
        """
        Store the desired image tag in Redis. The wake_worker reads this
        before spawning so the agent boots into the new container image.
        """
        await self._redis.set(f"image_tag:{darwin_id}", new_tag)

    async def get_image_tag(self, darwin_id: str) -> str:
        tag = await self._redis.get(f"image_tag:{darwin_id}")
        return tag or "darwin-agent:latest"
