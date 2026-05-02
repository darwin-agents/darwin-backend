"""
/assets — Binary file gateway (presigned S3/Supabase uploads).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from db.client import get_supabase
from deps import CurrentAgent, CurrentUser, PoolDep, SettingsDep

router = APIRouter(prefix="/assets", tags=["assets"])


class IndexRequest(BaseModel):
    asset_id: str
    darwin_id: str
    description: str | None = None


@router.get("/presigned-url")
async def get_presigned_url(
    darwin_id: str,
    filename: str,
    content_type: str,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """
    Generate a time-limited presigned URL for direct-to-Supabase-Storage upload.
    The file bypasses the backend RAM completely.
    """
    if agent["darwin_id"] != darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    import uuid as _uuid
    asset_id = str(_uuid.uuid4())
    storage_path = f"agents/{darwin_id}/{asset_id}/{filename}"

    sb = await get_supabase()
    res = await sb.storage.from_("darwin-assets").create_signed_upload_url(storage_path)
    upload_url = res.get("signedUrl") or res.get("signed_url", "")

    # Pre-create the asset row so we have the ID ready for indexing
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO assets (id, darwin_id, filename, content_type, storage_path)
            VALUES ($1, $2, $3, $4, $5)
            """,
            asset_id, darwin_id, filename, content_type, storage_path,
        )

    return {"upload_url": upload_url, "asset_id": asset_id}


@router.post("/index", status_code=status.HTTP_200_OK)
async def index_asset(
    body: IndexRequest,
    agent: CurrentAgent,
    pool: PoolDep,
) -> dict:
    """Agent notifies backend that upload is complete. Links asset to agent row."""
    if agent["darwin_id"] != body.darwin_id:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE assets SET description = $2 WHERE id = $1 AND darwin_id = $3",
            body.asset_id, body.description, body.darwin_id,
        )
    return {"indexed": True, "asset_id": body.asset_id}


@router.get("/{asset_id}")
async def get_asset(
    asset_id: str,
    user: CurrentUser,
    pool: PoolDep,
) -> dict:
    """Returns a signed download URL for the creator to retrieve an artifact."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM assets a JOIN darwins d ON d.id = a.darwin_id WHERE a.id = $1 AND d.user_id = $2",
            asset_id, user["sub"],
        )
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    sb = await get_supabase()
    signed = await sb.storage.from_("darwin-assets").create_signed_url(row["storage_path"], 3600)
    return {**dict(row), "download_url": signed.get("signedUrl")}
