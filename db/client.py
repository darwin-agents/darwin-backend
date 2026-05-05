"""
Async database client.

Uses asyncpg directly for all SQL that requires transactions and row-level locking.
Supabase client (supabase-py) is used for Auth helpers and Storage presigned URLs.
"""

from __future__ import annotations

from urllib.parse import urlparse

import asyncpg
from supabase import AsyncClient, acreate_client

from config import get_settings

_pool: asyncpg.Pool | None = None  # type: ignore[type-arg]
_supabase: AsyncClient | None = None


async def get_pool() -> asyncpg.Pool:  # type: ignore[type-arg]
    global _pool
    if _pool is None:
        settings = get_settings()
        host = (urlparse(settings.database_url).hostname or "").lower()
        # Supabase pooler (PgBouncer transaction mode) is incompatible with
        # asyncpg prepared statement cache; disable cache in that topology.
        stmt_cache_size = 0 if "pooler.supabase.com" in host else 100
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=20,
            command_timeout=30,
            statement_cache_size=stmt_cache_size,
            init=_init_connection,
        )
    return _pool


async def _init_connection(conn: asyncpg.Connection) -> None:  # type: ignore[type-arg]
    """Register the pgvector codec and set statement timeout."""
    await conn.execute("SET statement_timeout = '25s'")


async def get_supabase() -> AsyncClient:
    global _supabase
    if _supabase is None:
        settings = get_settings()
        _supabase = await acreate_client(
            settings.supabase_url,
            settings.supabase_secret_key.get_secret_value(),
        )
    return _supabase


async def close_all() -> None:
    global _pool, _supabase
    if _pool:
        await _pool.close()
        _pool = None
    _supabase = None
