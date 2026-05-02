# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── Dependencies ──────────────────────────────────────────────────────────────
FROM base AS deps
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ── Production image ──────────────────────────────────────────────────────────
FROM deps AS production
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
