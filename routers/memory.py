"""
/memory — Genetic Repository (Skill Documents + pgvector semantic search).
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from deps import CurrentAgent, PoolDep, SettingsDep
from schemas.memory import RoyaltyRequest, SkillQueryRequest, SkillQueryResult, SkillUploadRequest
from services.canvas_service import CanvasService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_skill(
    body: SkillUploadRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """
    Ingest a Procedural Skill Document.
    1. Generate embedding via OpenAI text-embedding-3-small.
    2. Upsert into hermes_skills with pgvector.
    3. Emit SKILL_CREATED canvas event.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    embedding = await _embed(body.content, settings)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO hermes_skills (author_darwin_id, title, content, embedding, royalty_usdc)
            VALUES ($1, $2, $3, $4::vector, $5)
            RETURNING id
            """,
            body.darwin_id, body.title, body.content,
            str(embedding) if embedding else None,
            body.royalty_usdc,
        )
        skill_id = str(row["id"])

    canvas = CanvasService(pool)
    asyncio.create_task(canvas.push_event(
        darwin_id=body.darwin_id,
        session_id=agent["session_id"],
        event_type="SKILL_CREATED",
        payload={
            "skill_id": skill_id,
            "title": body.title,
            "content_preview": body.content[:150],
            "royalty_usdc": body.royalty_usdc,
        },
    ))
    return {"skill_id": skill_id, "title": body.title}


@router.post("/query", response_model=list[SkillQueryResult])
async def query_skills(
    body: SkillQueryRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> list[SkillQueryResult]:
    """
    Semantic skill search via cosine similarity (pgvector).
    Emits SKILL_LOADED event for each result used on the canvas.
    """
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    embedding = await _embed(body.query, settings)
    if not embedding:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, content, author_darwin_id, royalty_usdc,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM hermes_skills
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            str(embedding), body.top_k,
        )

    results = [
        SkillQueryResult(
            skill_id=r["id"],
            title=r["title"],
            content=r["content"],
            similarity_score=float(r["similarity"]),
            author_darwin_id=r["author_darwin_id"],
            royalty_usdc=float(r["royalty_usdc"]),
        )
        for r in rows
    ]

    canvas = CanvasService(pool)
    for r in results:
        asyncio.create_task(canvas.push_event(
            darwin_id=body.darwin_id,
            session_id=agent["session_id"],
            event_type="SKILL_LOADED",
            payload={
                "skill_id": str(r.skill_id),
                "title": r.title,
                "similarity_score": r.similarity_score,
                "author_darwin_id": str(r.author_darwin_id) if r.author_darwin_id else None,
            },
        ))

    return results


@router.post("/royalty", status_code=status.HTTP_200_OK)
async def pay_royalty(
    body: RoyaltyRequest,
    agent: CurrentAgent,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Pay a micro-royalty from consumer agent to skill author."""
    if agent["darwin_id"] != body.consumer_darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        skill = await conn.fetchrow(
            "SELECT author_darwin_id, royalty_usdc FROM hermes_skills WHERE id = $1",
            body.skill_id,
        )
        if not skill or not skill["author_darwin_id"]:
            return {"skipped": True, "reason": "no_author"}

        await conn.execute(
            "UPDATE hermes_skills SET usage_count = usage_count + 1 WHERE id = $1",
            body.skill_id,
        )

    from decimal import Decimal
    from schemas.finance import LedgerUpdateRequest
    from services.ledger_service import LedgerService

    ledger = LedgerService(pool, settings)
    royalty = Decimal(str(skill["royalty_usdc"]))
    session_id = agent["session_id"]

    from services.ledger_service import InsufficientFundsError
    try:
        await ledger.apply_transaction(LedgerUpdateRequest(
            darwin_id=body.consumer_darwin_id,
            session_id=session_id,
            amount=-royalty,
            type="ROYALTY_DEBIT",
            description=f"Royalty for skill {body.skill_id}",
        ))
        await ledger.apply_transaction(LedgerUpdateRequest(
            darwin_id=str(skill["author_darwin_id"]),
            session_id=session_id,
            amount=royalty,
            type="ROYALTY_CREDIT",
            description=f"Royalty income from skill usage",
            external_ref=body.skill_id,
        ))
    except InsufficientFundsError:
        return {"skipped": True, "reason": "insufficient_funds"}

    return {"paid": True, "amount_usdc": float(royalty)}


async def _embed(text: str, settings) -> list[float] | None:  # type: ignore[type-ignore]
    """Generate OpenAI text-embedding-3-small vector. Returns None if no key configured."""
    key = settings.openai_api_key.get_secret_value()
    if not key:
        return None

    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "text-embedding-3-small", "input": text},
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
