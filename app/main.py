"""
Darwin Backend — The God Layer
FastAPI application entry point.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import close_connections, get_pg_pool, get_supabase
from app.routers import (
    assets,
    audit,
    contracts,
    darwins,
    finance,
    firmware,
    gateway,
    identity,
    memory,
    swarms,
    telemetry,
    webhooks,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Warm up persistent connections on startup; drain them on shutdown."""
    settings = get_settings()
    logger.info("darwin_backend.starting", env=settings.environment)

    await get_supabase()
    await get_pg_pool()

    logger.info("darwin_backend.ready")
    yield

    await close_connections()
    logger.info("darwin_backend.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Darwin Backend — The God Layer",
        description=(
            "Orchestrates a swarm of autonomous economic agents. "
            "Manages lifecycle, finance, telemetry, contracts, and evolutionary upgrades."
        ),
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware ─────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global exception handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}},
        )

    # ── Health ─────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["health"], include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "version": settings.app_version}

    # ── API Routers ────────────────────────────────────────────────────────────
    prefix = settings.api_v1_prefix
    app.include_router(darwins.router, prefix=prefix)
    app.include_router(finance.router, prefix=prefix)
    app.include_router(webhooks.router, prefix=prefix)
    app.include_router(memory.router, prefix=prefix)
    app.include_router(contracts.router, prefix=prefix)
    app.include_router(telemetry.router, prefix=prefix)
    app.include_router(assets.router, prefix=prefix)
    app.include_router(swarms.router, prefix=prefix)
    app.include_router(audit.router, prefix=prefix)
    app.include_router(identity.router, prefix=prefix)
    app.include_router(gateway.router, prefix=prefix)
    app.include_router(firmware.router, prefix=prefix)

    return app


app = create_app()
