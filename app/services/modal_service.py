"""
ModalService — thin wrapper around the Modal Python SDK.

Handles container provisioning, hibernation signals, and Kill Switch execution.
"""

from __future__ import annotations

import modal

from app.config import Settings


class ModalService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = modal.Client.from_credentials(
            token_id=settings.modal_token_id.get_secret_value(),
            token_secret=settings.modal_token_secret.get_secret_value(),
        )

    async def spawn_container(
        self,
        agent_id: str,
        docker_image_tag: str,
        directive: str,
        wake_up_prompt: str | None = None,
        volume_name: str | None = None,
    ) -> str:
        """
        Provision an ephemeral Modal container for a Darwin Agent.
        Returns the Modal container run ID.
        """
        # TODO: implement using modal.Function.lookup() + .spawn()
        raise NotImplementedError

    async def terminate_container(self, container_run_id: str) -> bool:
        """
        Instantly incinerate a running container (Kill Switch).
        Returns True if the container was stopped.
        """
        raise NotImplementedError

    async def update_image_tag(self, agent_id: str, new_image_tag: str) -> None:
        """
        Update the Docker image tag in the agent's Modal provisioning profile
        so the next wake-up boots into the new Hermes version.
        """
        raise NotImplementedError

    async def inject_migration_script(self, agent_id: str, script: str) -> None:
        """Queue a Python migration script to run on next container start-up."""
        raise NotImplementedError
