"""
/audit — Append-only compliance record and tax export.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from deps import CurrentAgent, CurrentUser, PoolDep

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditLogRequest(BaseModel):
    darwin_id: str
    event_type: str
    prompt_snapshot: str | None = None
    context_snapshot: dict | None = None
    decision: str
    financial_impact: float | None = None


@router.post("/log", status_code=status.HTTP_201_CREATED)
async def log_action(
    body: AuditLogRequest,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """Append-only financial decision record. Called before every significant agent action."""
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO audit_log
              (darwin_id, event_type, prompt_snapshot, context_snapshot, decision, financial_impact)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6)
            RETURNING id, created_at
            """,
            body.darwin_id,
            body.event_type,
            body.prompt_snapshot,
            json.dumps(body.context_snapshot) if body.context_snapshot else None,
            body.decision,
            body.financial_impact,
        )
    return {"log_id": str(row["id"]), "created_at": row["created_at"].isoformat()}


@router.get("/logs")
async def get_logs(
    user: CurrentUser,
    pool: PoolDep,
    darwin_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = """
        SELECT al.* FROM audit_log al
        JOIN darwins d ON d.id = al.darwin_id
        WHERE d.user_id = $1
    """
    params: list = [user["sub"]]

    if darwin_id:
        params.append(darwin_id)
        query += f" AND al.darwin_id = ${len(params)}"
    if event_type:
        params.append(event_type)
        query += f" AND al.event_type = ${len(params)}"

    params.extend([limit, offset])
    query += f" ORDER BY al.created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


@router.get("/export")
async def export_tax(
    user: CurrentUser,
    pool: PoolDep,
    darwin_id: str | None = None,
    from_date: str = "2024-01-01",
    to_date: str = "2099-12-31",
    format: str = "json",
) -> dict:
    """
    Generate tax document — aggregate fiat revenue, compute write-offs, USDC gains.
    """
    query = """
        SELECT
          COALESCE(SUM(lt.amount) FILTER (WHERE lt.amount > 0), 0) AS total_revenue,
          COALESCE(SUM(ABS(lt.amount)) FILTER (WHERE lt.amount < 0), 0) AS total_costs
        FROM ledger_transactions lt
        JOIN darwins d ON d.id = lt.darwin_id
        WHERE d.user_id = $1
          AND lt.created_at BETWEEN $2 AND $3
    """
    params: list = [user["sub"], from_date, to_date]

    if darwin_id:
        params.append(darwin_id)
        query += f" AND lt.darwin_id = ${len(params)}"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)

    revenue = float(row["total_revenue"] or 0)
    costs = float(row["total_costs"] or 0)

    return {
        "period": {"from": from_date, "to": to_date},
        "total_revenue_usd": revenue,
        "total_compute_costs_usd": costs,
        "net_profit_usd": revenue - costs,
        "format": format,
        "generated_at": datetime.utcnow().isoformat(),
    }
