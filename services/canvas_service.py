"""
CanvasService — centralised darwin_events INSERT.

Every service that needs to push something to the Infinite Canvas calls this.
Supabase Realtime handles fan-out to the frontend automatically.
"""

from __future__ import annotations

import time

import asyncpg


class CanvasService:
    def __init__(self, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._pool = pool

    async def push_event(
        self,
        darwin_id: str,
        session_id: str,
        event_type: str,
        payload: dict,
        neuron_id: str | None = None,
        parent_event_id: str | None = None,
        token_cost: float = 0.0,
        fiat_cost: float = 0.0,
    ) -> str:
        """
        Insert a darwin_events row. Supabase Realtime fans it out to the frontend.
        Returns the new event UUID.
        """
        import json

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO darwin_events
                  (darwin_id, session_id, neuron_id, parent_event_id,
                   event_type, payload, timestamp_ms, token_cost, fiat_cost)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9)
                RETURNING id
                """,
                darwin_id,
                session_id,
                neuron_id,
                parent_event_id,
                event_type,
                json.dumps(payload),
                int(time.time() * 1000),
                token_cost,
                fiat_cost,
            )
        return str(row["id"])

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def push_neuron_spawned(
        self,
        darwin_id: str,
        session_id: str,
        neuron_id: str,
        task: str,
        parent_event_id: str,
    ) -> str:
        return await self.push_event(
            darwin_id=darwin_id,
            session_id=session_id,
            event_type="NEURON_SPAWNED",
            neuron_id=neuron_id,
            parent_event_id=parent_event_id,
            payload={"neuron_id": neuron_id, "task": task, "parent_event_id": parent_event_id},
        )

    async def push_synaptic_integration(
        self,
        darwin_id: str,
        session_id: str,
        merged: list[str],
        failed: list[str],
        synthesis: str,
    ) -> str:
        return await self.push_event(
            darwin_id=darwin_id,
            session_id=session_id,
            event_type="SYNAPTIC_INTEGRATION",
            payload={
                "neuron_ids_merged": merged,
                "failed_neurons": failed,
                "synthesis": synthesis,
            },
        )

    async def push_lifecycle(
        self,
        darwin_id: str,
        session_id: str,
        event_type: str,
        payload: dict,
    ) -> str:
        return await self.push_event(
            darwin_id=darwin_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
