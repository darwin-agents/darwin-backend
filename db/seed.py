"""Dev seed data — run once against a local/dev Supabase instance."""

from __future__ import annotations

import asyncio

from db.client import get_pool


async def seed() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        print("Seed complete (no-op for production). Add dev fixtures here.")


if __name__ == "__main__":
    asyncio.run(seed())
