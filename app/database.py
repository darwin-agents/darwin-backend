"""
Database session management.

Uses supabase-py for the Supabase client (auth helpers, storage, realtime) and
an asyncpg-backed connection pool for raw async SQL queries requiring row-level
locking (e.g. SELECT … FOR UPDATE in the finance ledger).
"""

from __future__ import annotations

import asyncpg
from supabase import AsyncClient, acreate_client

from app.config import Settings, get_settings

_supabase_client: AsyncClient | None = None
_pg_pool: asyncpg.Pool | None = None  # type: ignore[type-arg]


async def get_supabase() -> AsyncClient:
    global _supabase_client
    if _supabase_client is None:
        cfg: Settings = get_settings()
        _supabase_client = await acreate_client(
            cfg.supabase_url,
            cfg.supabase_key.get_secret_value(),
        )
    return _supabase_client


async def get_pg_pool() -> asyncpg.Pool:  # type: ignore[type-arg]
    global _pg_pool
    if _pg_pool is None:
        cfg: Settings = get_settings()
        _pg_pool = await asyncpg.create_pool(
            dsn=cfg.database_url,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
    return _pg_pool


async def close_connections() -> None:
    global _supabase_client, _pg_pool
    if _supabase_client is not None:
        await _supabase_client.auth.sign_out()
        _supabase_client = None
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
