"""
/assets — Digital Artifact Gateway

Presigned S3/Supabase Storage uploads that bypass the God Layer's RAM.
Prevents large binary artifacts (CSV, PDF, images) from overwhelming the API server.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import CurrentAgent, CurrentUser, SupabaseDep
from app.schemas.assets import (
    AssetIndexRequest,
    AssetRecord,
    PresignedUrlRequest,
    PresignedUrlResponse,
)

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/presigned-url", response_model=PresignedUrlResponse)
async def get_presigned_url(
    agent_id: UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> PresignedUrlResponse:
    """
    Generate a time-limited presigned URL for direct-to-bucket upload.
    The file goes straight to Supabase Storage; the God Layer's RAM is never touched.
    """
    raise NotImplementedError


@router.post("/index", response_model=AssetRecord, status_code=status.HTTP_201_CREATED)
async def index_asset(
    body: AssetIndexRequest,
    _agent: CurrentAgent,
    _db: SupabaseDep,
) -> AssetRecord:
    """
    Called by the agent after completing a direct upload.
    Links the permanent bucket URL to the agent's database row.
    """
    raise NotImplementedError


@router.get("/{asset_id}", response_model=AssetRecord)
async def get_asset(
    asset_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> AssetRecord:
    raise NotImplementedError


@router.get("/", response_model=list[AssetRecord])
async def list_agent_assets(
    agent_id: UUID,
    _user: CurrentUser,
    _db: SupabaseDep,
) -> list[AssetRecord]:
    raise NotImplementedError
