"""
/audit — Compliance Engine

Immutable black-box flight recorder for every agent decision.
Survives IRS audits and regulatory review in Phase 2.
"""

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, SupabaseDep
from app.schemas.audit import (
    AuditLogEntry,
    AuditLogRequest,
    TaxExportRequest,
    TaxExportResponse,
)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.post("/log", response_model=AuditLogEntry, status_code=status.HTTP_201_CREATED)
async def log_action(
    body: AuditLogRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> AuditLogEntry:
    """
    Archive a financial decision with its full cognitive context.

    Creates an immutable audit record (INSERT-only, no UPDATE/DELETE).
    Used by the Compliance Engine for regulatory review and P&L forensics.
    """
    raise NotImplementedError


@router.get("/logs")
async def list_audit_logs(
    agent_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _user: CurrentUser = None,  # type: ignore[assignment]
    _db: SupabaseDep = None,  # type: ignore[assignment]
) -> list[AuditLogEntry]:
    raise NotImplementedError


@router.post("/export", response_model=TaxExportResponse)
async def export_tax_report(
    body: TaxExportRequest,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> TaxExportResponse:
    """
    Generate formatted tax documents for the fiscal year.

    Calculates aggregate fiat revenue, serverless compute write-offs,
    and crypto capital gains across the entire swarm.
    """
    raise NotImplementedError
