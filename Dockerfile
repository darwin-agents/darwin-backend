# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Install uv — the only package manager used in this project
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached layer — only invalidated when lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8000

# Default: FastAPI server
# Override CMD in Railway/Render for the Celery services:
#   uv run celery -A workers.app worker --loglevel=info --concurrency=50 -P gevent
#   uv run celery -A workers.app beat   --loglevel=info
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
