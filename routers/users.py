"""
/users — Creator accounts and encrypted credentials vault.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from config import Settings
from db.client import get_pool, get_supabase
from deps import CurrentUser, PoolDep, SettingsDep
from schemas.agents import SwarmStateResponse
from schemas.finance import VaultSubmitRequest, VaultSubmitResponse
from services.vcc_service import VCCService
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["users"])


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, settings: SettingsDep) -> dict:
    """Proxy to Supabase Auth signUp. Creates user_profiles row."""
    sb = await get_supabase()
    res = await sb.auth.sign_up({"email": body.email, "password": body.password})
    if res.user is None:
        raise HTTPException(status_code=400, detail="Registration failed")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_profiles (id) VALUES ($1) ON CONFLICT DO NOTHING",
            res.user.id,
        )
    return {"user_id": res.user.id, "access_token": res.session.access_token if res.session else None}


@router.post("/login")
async def login(body: LoginRequest, settings: SettingsDep) -> dict:
    """Proxy to Supabase Auth signInWithPassword."""
    sb = await get_supabase()
    res = await sb.auth.sign_in_with_password({"email": body.email, "password": body.password})
    if res.session is None:
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials"})
    return {
        "access_token": res.session.access_token,
        "refresh_token": res.session.refresh_token,
    }


@router.post("/{uid}/vault", response_model=VaultSubmitResponse)
async def submit_vault(
    uid: str,
    body: VaultSubmitRequest,
    user: CurrentUser,
    pool: PoolDep,
    settings: SettingsDep,
) -> VaultSubmitResponse:
    """
    Store encrypted credentials for an agent.

    For VCCs: PAN/CVV/expiry encrypted individually via pgcrypto.
    Returns an opaque ref token — the agent only ever sees this.
    """
    if user["sub"] != uid:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    if body.secret_type == "vcc":
        p = body.payload
        vcc_svc = VCCService(pool, settings)
        ref = await vcc_svc.store_vcc(
            darwin_id=body.darwin_id,
            user_id=uid,
            pan=p["pan"],
            cvv=p["cvv"],
            expiry=p["expiry"],
            spend_limit=float(p["spend_limit"]),
            provider=p.get("provider", "manual"),
        )
        return VaultSubmitResponse(ref=ref, spend_limit=float(p["spend_limit"]))

    # Other secret types (stripe_key, broker_api_key, iban) stored as encrypted text
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO vcc_vault
              (darwin_id, user_id, encrypted_pan, encrypted_cvv, encrypted_expiry,
               spend_limit, provider)
            VALUES ($1, $2,
              pgp_sym_encrypt($3, $6),
              pgp_sym_encrypt($4, $6),
              pgp_sym_encrypt($5, $6),
              0, $7)
            RETURNING id
            """,
            body.darwin_id, uid,
            str(body.payload),
            body.secret_type,
            "",
            settings.vcc_encryption_key.get_secret_value(),
            body.secret_type,
        )
    return VaultSubmitResponse(ref=str(row["id"]), spend_limit=0)


@router.get("/{uid}/swarm-state", response_model=SwarmStateResponse)
async def get_swarm_state(
    uid: str,
    user: CurrentUser,
    pool: PoolDep,
) -> SwarmStateResponse:
    """Aggregate P&L dashboard for the creator's swarm."""
    if user["sub"] != uid:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.name, d.state, d.ledger_balance, d.initial_budget,
                   d.total_revenue, d.token_roi, d.last_active_at
            FROM darwins d
            WHERE d.user_id = $1 AND d.state != 'TERMINATED'
            ORDER BY d.created_at DESC
            """,
            uid,
        )

    from schemas.agents import AgentSummary
    import uuid

    darwins = [
        AgentSummary(
            id=r["id"],
            name=r["name"],
            state=r["state"],
            ledger_balance=float(r["ledger_balance"]),
            initial_budget=float(r["initial_budget"]),
            total_revenue=float(r["total_revenue"] or 0),
            token_roi=float(r["token_roi"]) if r["token_roi"] else None,
            last_active_at=r["last_active_at"],
            current_session_id=None,
        )
        for r in rows
    ]

    total_revenue = sum(d.total_revenue for d in darwins)
    total_burned = sum(d.initial_budget - d.ledger_balance for d in darwins)

    return SwarmStateResponse(
        darwins=darwins,
        total_revenue=total_revenue,
        total_burned=total_burned,
    )
