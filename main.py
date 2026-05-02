"""
Darwin Backend — The God Layer.
FastAPI application factory, router registration, and lifespan events.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from db.client import close_all, get_pool

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("darwin_backend.starting", environment=settings.environment)
    await get_pool()  # warm up asyncpg connection pool
    logger.info("darwin_backend.ready")
    yield
    await close_all()
    logger.info("darwin_backend.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Darwin Backend — The God Layer",
        description=(
            "Always-on orchestrator that enforces the physical laws of the Darwin ecosystem. "
            "Manages agent lifecycle, fiat ledger, VCC vault, webhooks, telemetry, and the Infinite Canvas."
        ),
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global exception handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        trace_id = str(uuid4())
        logger.exception("unhandled_exception", path=request.url.path, trace_id=trace_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "trace_id": trace_id},
        )

    # ── Health check ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["health"], include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    # ── Routers ────────────────────────────────────────────────────────────────
    from routers import (
        assets,
        audit,
        contracts,
        darwins,
        finance,
        firmware,
        gateway,
        identity,
        memory,
        notifications,
        security,
        sessions,
        telemetry,
        users,
        webhooks,
    )

    prefix = settings.api_v1_prefix
    app.include_router(users.router, prefix=prefix)
    app.include_router(darwins.router, prefix=prefix)
    app.include_router(finance.router, prefix=prefix)
    app.include_router(webhooks.router, prefix=prefix)
    app.include_router(telemetry.router, prefix=prefix)
    app.include_router(memory.router, prefix=prefix)
    app.include_router(contracts.router, prefix=prefix)
    app.include_router(assets.router, prefix=prefix)
    app.include_router(audit.router, prefix=prefix)
    app.include_router(identity.router, prefix=prefix)
    app.include_router(gateway.router, prefix=prefix)
    app.include_router(firmware.router, prefix=prefix)
    app.include_router(notifications.router, prefix=prefix)
    app.include_router(sessions.router, prefix=prefix)
    app.include_router(security.router, prefix=prefix)

    return app


app = create_app()
