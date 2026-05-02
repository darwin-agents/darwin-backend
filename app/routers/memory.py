"""
/memory — Genetic Repository

Swarm-wide vector knowledge base. Agents upload Procedural Skill Documents,
query them via cosine similarity, and earn micro-royalties for shared knowledge.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, SupabaseDep
from app.schemas.memory import (
    MemoryQueryRequest,
    MemoryQueryResponse,
    RoyaltyRequest,
    SkillDocument,
    SkillUploadRequest,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/upload", response_model=SkillDocument, status_code=status.HTTP_201_CREATED)
async def upload_skill(
    body: SkillUploadRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> SkillDocument:
    """
    Ingest a Procedural Skill Document.

    1. Embed the content via the gateway (OpenAI text-embedding-3-small).
    2. Upsert the vector into Supabase pgvector.
    3. Index the document metadata.
    """
    raise NotImplementedError


@router.post("/query", response_model=MemoryQueryResponse)
async def query_memory(
    body: MemoryQueryRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> MemoryQueryResponse:
    """
    Semantic search across the swarm's collective knowledge.

    Runs cosine similarity search via pgvector before an agent wastes tokens
    brute-forcing an interface it has already solved.
    """
    raise NotImplementedError


@router.post("/royalty", status_code=status.HTTP_200_OK)
async def trigger_royalty(
    body: RoyaltyRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> dict:
    """
    Route a micro-royalty payment from the consuming agent to the skill author.
    Calls the /finance ledger internally.
    """
    raise NotImplementedError


@router.get("/{skill_id}", response_model=SkillDocument)
async def get_skill(
    skill_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> SkillDocument:
    raise NotImplementedError


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> None:
    raise NotImplementedError
