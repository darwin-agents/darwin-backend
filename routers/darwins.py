"""
/darwins — Agent Lifecycle Manager.

All FSM state transitions are enforced here. The only valid paths are:
  PENDING → ACTIVE → HIBERNATING ↔ ACTIVE → TERMINATED
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status

from config import Settings
from deps import CurrentAgent, CurrentUser, PoolDep, SettingsDep, mint_agent_jwt
from schemas.agents import (
    AgentStateResponse,
    ConfigPatchRequest,
    HibernateRequest,
    SpawnRequest,
    SpawnResponse,
    UplinkRequest,
)
from services.canvas_service import CanvasService
from services.intent_scan_service import IntentScanService
from services.modal_service import ModalService
from services.telegram_service import TelegramService

router = APIRouter(prefix="/darwins", tags=["darwins"])

# Valid FSM transitions
_FSM: dict[str, set[str]] = {
    "PENDING":     {"ACTIVE", "TERMINATED"},
    "ACTIVE":      {"HIBERNATING", "TERMINATED"},
    "HIBERNATING": {"ACTIVE", "TERMINATED"},
    "TERMINATED":  set(),
}


def _assert_transition(current: str, target: str, darwin_id: str) -> None:
    if target not in _FSM.get(current, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "invalid_state_transition", "current_state": current},
        )


@router.post("/spawn", response_model=SpawnResponse, status_code=status.HTTP_201_CREATED)
async def spawn_agent(
    body: SpawnRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> SpawnResponse:
    """
    Create and launch a new Darwin Agent.

    1. Pre-flight intent scan (abuse classifier).
    2. INSERT darwins row (PENDING).
    3. Spawn Modal container.
    4. Transition to ACTIVE.
    5. Emit AGENT_SPAWNED canvas event.
    """
    user_id = user["sub"]

    # 1. Intent scan
    scanner = IntentScanService(settings)
    flagged, reason = await scanner.scan_directive(body.directive)
    if flagged:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (darwin_id, event_type, decision)
                VALUES (gen_random_uuid(), 'INTENT_FLAGGED', $1)
                """,
                f"Rejected directive: {reason}",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "directive_rejected", "reason": reason},
        )

    session_id = str(uuid4())
    darwin_id: str

    # 2. Create agent row
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO darwins
              (user_id, name, state, system_prompt, llm_model,
               tool_set, vcc_ref, ledger_balance, initial_budget, requires_approval)
            VALUES ($1, $2, 'PENDING', $3, $4, $5::jsonb, $6, $7, $7, $8)
            RETURNING id
            """,
            user_id,
            body.name,
            body.directive,
            body.llm_model,
            __import__("json").dumps(body.tool_set),
            body.vcc_ref,
            body.initial_budget,
            body.requires_approval,
        )
        darwin_id = str(row["id"])

    # 3. Mint agent JWT and spawn container
    agent_jwt = mint_agent_jwt(darwin_id, session_id, settings)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)

    try:
        modal_call_id = await modal.spawn_container(
            darwin_id=darwin_id,
            session_id=session_id,
            wake_prompt=f"You are a Darwin Agent. Your directive: {body.directive}",
            agent_jwt=agent_jwt,
        )
    except Exception as exc:
        # Rollback to TERMINATED if Modal fails
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE darwins SET state = 'TERMINATED', terminated_at = now() WHERE id = $1",
                darwin_id,
            )
        raise HTTPException(status_code=500, detail={"error": "modal_spawn_failed", "detail": str(exc)}) from exc

    # 4. Transition to ACTIVE
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE darwins SET state = 'ACTIVE', spawned_at = now(), modal_call_id = $2 WHERE id = $1",
            darwin_id, modal_call_id,
        )

    # 5. Emit canvas event (fire-and-forget)
    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_lifecycle(
        darwin_id=darwin_id,
        session_id=session_id,
        event_type="AGENT_SPAWNED",
        payload={
            "name": body.name,
            "initial_budget": body.initial_budget,
            "llm_model": body.llm_model,
            "system_prompt_preview": body.directive[:200],
            "tool_set": body.tool_set,
        },
    ))

    return SpawnResponse(darwin_id=UUID(darwin_id), session_id=UUID(session_id), state="ACTIVE")


@router.get("/{darwin_id}/state", response_model=AgentStateResponse)
async def get_state(darwin_id: str, user: CurrentUser, pool: PoolDep) -> AgentStateResponse:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT d.state, d.ledger_balance, d.last_active_at,
                   de.session_id AS current_session_id
            FROM darwins d
            LEFT JOIN LATERAL (
              SELECT session_id FROM darwin_events
              WHERE darwin_id = d.id ORDER BY created_at DESC LIMIT 1
            ) de ON TRUE
            WHERE d.id = $1 AND d.user_id = $2
            """,
            darwin_id, user["sub"],
        )
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    return AgentStateResponse(
        state=row["state"],
        ledger_balance=float(row["ledger_balance"]),
        burn_rate_per_hour=0.0,  # TODO: compute from telemetry Redis time-series
        current_session_id=row["current_session_id"],
        last_event_at=row["last_active_at"],
    )


@router.get("/{darwin_id}/logs")
async def get_logs(
    darwin_id: str,
    session_id: str,
    neuron_id: str | None = None,
    user: CurrentUser = None,  # type: ignore[assignment]
    settings: SettingsDep = None,  # type: ignore[assignment]
) -> dict:
    """Fetch structured thought log from LangSmith (cached 5 min in Redis)."""
    import redis.asyncio as aioredis
    from services.langsmith_service import LangSmithService

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    ls = LangSmithService(settings, redis)
    trace = await ls.get_trace(session_id, neuron_id)
    return {"session_id": session_id, "neuron_id": neuron_id, "trace": trace}


@router.post("/{darwin_id}/hibernate", status_code=status.HTTP_200_OK)
async def hibernate(
    darwin_id: str,
    body: HibernateRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Agent-initiated hibernation.
    Saves episodic summary, registers wake trigger, transitions state.
    """
    if agent["darwin_id"] != darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state, name FROM darwins WHERE id = $1", darwin_id)
        if not row:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        _assert_transition(row["state"], "HIBERNATING", darwin_id)

        wake = body.wake_trigger
        trigger_at = wake.get("trigger_at") if wake.get("type") == "CRON" else None
        webhook_key = wake.get("webhook_key") if wake.get("type") == "WEBHOOK" else None

        await conn.execute(
            """
            INSERT INTO hibernate_schedule
              (darwin_id, trigger_type, trigger_at, webhook_key, wake_prompt, browserbase_session_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            darwin_id, wake.get("type", "CRON"), trigger_at, webhook_key,
            f"You were hibernating. Reason: {body.reason}. Resume.",
            body.browserbase_session_id,
        )
        await conn.execute(
            "UPDATE darwins SET state = 'HIBERNATING' WHERE id = $1", darwin_id
        )

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_lifecycle(
        darwin_id=darwin_id,
        session_id=str(body.session_id),
        event_type="AGENT_HIBERNATED",
        payload={
            "reason": body.reason,
            "wake_trigger_type": wake.get("type"),
            "wake_trigger_at": wake.get("trigger_at"),
            "episodic_summary": body.episodic_summary,
            "browserbase_session_kept_alive": body.browserbase_session_id is not None,
        },
    ))

    return {"hibernated": True}


@router.post("/{darwin_id}/wake", status_code=status.HTTP_202_ACCEPTED)
async def wake_agent(darwin_id: str, user: CurrentUser, pool: PoolDep) -> dict:
    """Backend-initiated wake (also triggered by Celery workers)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state FROM darwins WHERE id = $1", darwin_id)
        if not row:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if row["state"] != "HIBERNATING":
            raise HTTPException(
                status_code=409,
                detail={"error": "invalid_state_transition", "current_state": row["state"]},
            )

    from workers.wake_worker import execute_agent_wake
    execute_agent_wake.delay(darwin_id, "Manual wake initiated by creator.", "manual")
    return {"queued": True}


@router.post("/{darwin_id}/terminate", status_code=status.HTTP_200_OK)
async def terminate(darwin_id: str, user: CurrentUser, pool: PoolDep, settings: SettingsDep) -> dict:
    """Kill switch — terminates container, freezes VCC, emits event."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, name, vcc_ref FROM darwins WHERE id = $1 AND user_id = $2",
            darwin_id, user["sub"],
        )
        if not row:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if row["state"] == "TERMINATED":
            raise HTTPException(status_code=409, detail={"error": "already_terminated"})

        await conn.execute(
            "UPDATE darwins SET state = 'TERMINATED', terminated_at = now() WHERE id = $1",
            darwin_id,
        )
        total = await conn.fetchrow(
            "SELECT ledger_balance, total_revenue, initial_budget FROM darwins WHERE id = $1",
            darwin_id,
        )

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)
    await modal.kill_container(darwin_id)

    telegram = TelegramService(settings)
    canvas = CanvasService(pool)
    session_id = str(uuid4())

    asyncio.create_task(canvas.push_lifecycle(
        darwin_id=darwin_id,
        session_id=session_id,
        event_type="AGENT_TERMINATED",
        payload={
            "reason": "Kill switch — human initiated",
            "final_balance": float(total["ledger_balance"]) if total else 0,
            "total_revenue": float(total["total_revenue"] or 0) if total else 0,
            "total_burned": float(total["initial_budget"] - total["ledger_balance"]) if total else 0,
            "token_roi": None,
        },
    ))
    asyncio.create_task(telegram.send_alert_by_darwin_id(
        pool, darwin_id, f"🔴 Agent <b>{row['name']}</b> has been terminated."
    ))

    return {"terminated": True}


@router.post("/{darwin_id}/uplink", status_code=status.HTTP_202_ACCEPTED)
async def uplink(
    darwin_id: str,
    body: UplinkRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Cognitive Uplink — inject mid-lifecycle prompt into a running agent."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state FROM darwins WHERE id = $1 AND user_id = $2",
            darwin_id, user["sub"],
        )
    if not row or row["state"] != "ACTIVE":
        raise HTTPException(status_code=409, detail={"error": "agent_not_active"})

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    modal = ModalService(settings, redis)
    await modal.publish_uplink(darwin_id, body.message, body.attachments)

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_event(
        darwin_id=darwin_id,
        session_id=str(uuid4()),
        event_type="UPLINK_INJECTED",
        payload={"message": body.message, "attachments": body.attachments},
    ))
    return {"injected": True}


@router.post("/{darwin_id}/cron", status_code=status.HTTP_201_CREATED)
async def create_cron_job(
    darwin_id: str,
    body: dict,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Schedule a recurring cron wake-up for this Darwin.
    Called by the agent's schedule_cron() tool.

    Body: { "name": str, "cron_expr": str, "wake_prompt": str, "enabled": bool }
    """
    if agent["darwin_id"] != darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    import croniter
    from datetime import datetime, timezone

    name = body.get("name", "")
    cron_expr = body.get("cron_expr", "")
    wake_prompt = body.get("wake_prompt", "Scheduled check-in.")
    enabled = body.get("enabled", True)

    # Validate cron expression
    try:
        it = croniter.croniter(cron_expr, datetime.now(timezone.utc))
        next_run = it.get_next(datetime)
    except Exception:
        raise HTTPException(status_code=422, detail={"error": "invalid_cron_expression"})

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO darwin_cron_jobs (darwin_id, name, cron_expr, wake_prompt, enabled, next_run_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (darwin_id, name)
            DO UPDATE SET cron_expr = EXCLUDED.cron_expr,
                          wake_prompt = EXCLUDED.wake_prompt,
                          enabled = EXCLUDED.enabled,
                          next_run_at = EXCLUDED.next_run_at,
                          updated_at = now()
            RETURNING id, next_run_at
            """,
            darwin_id, name, cron_expr, wake_prompt, enabled, next_run,
        )

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_event(
        darwin_id=darwin_id,
        session_id=body.get("session_id", str(uuid4())),
        event_type="CRON_SCHEDULED",
        payload={"name": name, "cron_expr": cron_expr, "next_run_at": str(next_run)},
    ))
    return {
        "cron_id": str(row["id"]),
        "next_run_utc": row["next_run_at"].isoformat(),
    }


@router.delete("/{darwin_id}/cron/{name}", status_code=status.HTTP_200_OK)
async def cancel_cron_job(
    darwin_id: str,
    name: str,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """Cancel a named cron job."""
    if agent["darwin_id"] != darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE darwin_cron_jobs SET enabled = FALSE WHERE darwin_id = $1 AND name = $2",
            darwin_id, name,
        )
    return {"cancelled": result == "UPDATE 1"}


@router.get("/{darwin_id}/cron", status_code=status.HTTP_200_OK)
async def list_cron_jobs(
    darwin_id: str,
    agent: CurrentAgent,
    pool: PoolDep,
) -> list[dict]:
    """List all cron jobs for this Darwin."""
    if agent["darwin_id"] != darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, cron_expr, wake_prompt, enabled, next_run_at "
            "FROM darwin_cron_jobs WHERE darwin_id = $1 ORDER BY created_at DESC",
            darwin_id,
        )
    return [
        {
            "cron_id": str(r["id"]),
            "name": r["name"],
            "cron_expr": r["cron_expr"],
            "wake_prompt": r["wake_prompt"],
            "enabled": r["enabled"],
            "next_run_utc": r["next_run_at"].isoformat() if r["next_run_at"] else None,
        }
        for r in rows
    ]


@router.patch("/{darwin_id}/config", status_code=status.HTTP_200_OK)
async def patch_config(
    darwin_id: str,
    body: ConfigPatchRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Autonomy toggle and metabolic injection from the Command Console."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, initial_budget, ledger_balance, vcc_ref FROM darwins WHERE id = $1 AND user_id = $2",
            darwin_id, user["sub"],
        )
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    updates: list[str] = []
    params: list = [darwin_id]

    if body.requires_approval is not None:
        params.append(body.requires_approval)
        updates.append(f"requires_approval = ${len(params)}")

    if body.budget_injection is not None:
        from decimal import Decimal
        from schemas.finance import LedgerUpdateRequest
        from services.ledger_service import LedgerService

        ledger = LedgerService(pool, settings)
        await ledger.apply_transaction(
            LedgerUpdateRequest(
                darwin_id=darwin_id,
                session_id=str(uuid4()),
                amount=Decimal(str(body.budget_injection)),
                type="METABOLIC_INJECTION",
                description=f"Budget injection: +${body.budget_injection}",
            )
        )

    if updates:
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE darwins SET {', '.join(updates)} WHERE id = $1",
                *params,
            )

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_event(
        darwin_id=darwin_id,
        session_id=str(uuid4()),
        event_type="CONFIG_UPDATED",
        payload={
            "requires_approval": body.requires_approval,
            "budget_injection": body.budget_injection,
        },
    ))
    return {"updated": True}
