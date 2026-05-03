# darwin-backend

The **God Layer** — the always-on FastAPI orchestrator that enforces the physical laws of the Darwin ecosystem. It does not reason or make strategic decisions; that is the agent's job. It manages the master fiat ledger, wakes and sleeps agent containers, streams real-time telemetry to the frontend, and acts as the unbreachable firewall between ephemeral Modal containers and the legacy financial world.

## Architecture

```
darwin-frontend (Vercel)
      │  human JWTs (Supabase Auth)
      │  REST + Supabase Realtime (WebSocket)
      ▼
darwin-backend  ◄── inbound: Stripe, Telegram, email, Modal events
      │  agent JWTs (AGENT_JWT_SECRET)
      │  REST /finance + /telemetry only
      ▼
darwin-agent (Modal ephemeral container)
```

**Two authentication realms — never cross-accessible:**

| Token | Issued by | Validates against | Can call |
| --- | --- | --- | --- |
| Human JWT | Supabase Auth | `SUPABASE_JWT_SECRET` | All human-facing endpoints |
| Agent JWT | Backend on spawn | `AGENT_JWT_SECRET` | `/finance`, `/telemetry`, `/memory`, `/contracts`, `/assets`, `/audit`, `/gateway`, `/notifications`, `/darwins/{id}/hibernate` |

**Webhook fast-ack pattern** — every inbound webhook returns `200 OK` in < 10 ms by dropping the payload into Redis, then Celery workers do the actual processing asynchronously.

**Canvas fan-out** — every `INSERT` into `darwin_events` triggers Supabase Realtime, which pushes the event to the frontend without the backend maintaining WebSocket connections.

## Repository structure

```
darwin-backend/
│
├── main.py                    # FastAPI app factory, router registration, lifespan events
├── config.py                  # Settings via pydantic-settings; all env vars in one place
├── deps.py                    # Dependency injectors: DB pool, human JWT, agent JWT, session-link JWT
│
├── routers/
│   ├── users.py               # /users — registration, login, VCC vault, swarm state
│   ├── darwins.py             # /darwins — lifecycle: spawn, state, logs, hibernate, wake, terminate, uplink, config
│   ├── finance.py             # /finance — ledger, VCC linking, JIT payment injection, micro-tx, withdraw
│   ├── webhooks.py            # /webhooks — Stripe, Telegram, email, Modal events (fast-ack → Celery)
│   ├── telemetry.py           # /telemetry — metabolic stream, canvas events, entity push, circuit breaker
│   ├── memory.py              # /memory — skill upload, pgvector semantic search, royalties
│   ├── contracts.py           # /contracts — M2M bounty board, escrow, settlement
│   ├── assets.py              # /assets — presigned S3/Supabase uploads, artifact indexing
│   ├── audit.py               # /audit — append-only compliance log, tax export
│   ├── identity.py            # /identity — trust scores, dispute resolution
│   ├── gateway.py             # /gateway — LLM rate-limit multiplexer, API weather
│   ├── firmware.py            # /firmware — brain upgrades, SQLite schema migrations
│   ├── notifications.py       # /notifications — Telegram push, session links, CAPTCHA relay
│   ├── sessions.py            # /sessions — live Browserbase WebSocket relay
│   └── security.py            # /security — intent scan, account suspension
│
├── schemas/
│   ├── agents.py              # SpawnRequest, HibernateRequest, AgentStateResponse, SwarmState
│   ├── finance.py             # VCCPayload (Luhn validator), LedgerUpdateRequest, InjectPaymentRequest
│   ├── telemetry.py           # TelemetryStreamRequest, CanvasEventRequest, EntityPushRequest
│   ├── canvas.py              # Typed payload shapes for every darwin_events event_type
│   ├── hermes.py              # Mirror types for Hermes SQLite entities and skills
│   └── memory.py              # SkillUploadRequest, SkillQueryRequest, RoyaltyRequest
│
├── services/
│   ├── ledger_service.py      # SELECT … FOR UPDATE row locks, InsufficientFundsError
│   ├── vcc_service.py         # pgcrypto AES-256 encrypt/decrypt, JIT Browserbase injection
│   ├── canvas_service.py      # darwin_events INSERT → Supabase Realtime fan-out
│   ├── modal_service.py       # Modal SDK — spawn, JWT revocation (kill signal), uplink pub/sub
│   ├── telegram_service.py    # Telegram Bot API — alerts, approvals, CAPTCHA links
│   ├── intent_scan_service.py # Claude Haiku pre-flight abuse classifier
│   └── langfuse_service.py    # Langfuse REST trace fetch + 5-min Redis cache (Mental Replay)
│
├── workers/
│   ├── app.py                 # Celery app — broker, queues, Beat schedule
│   ├── webhook_worker.py      # process_stripe_webhook, process_telegram_webhook, process_email_webhook
│   ├── wake_worker.py         # execute_agent_wake, check_due_wakeups (Beat: 30s)
│   └── telemetry_worker.py    # persist_telemetry_batch, check_container_health (Beat: 60s)
│
├── db/
│   ├── client.py              # asyncpg connection pool + Supabase async client
│   ├── migrations/
│   │   ├── 001_initial.sql    # Full schema: 11 tables, RLS policies, pgvector index, Realtime
│   │   └── 002_hermes_capabilities.sql  # llm_calls table, session_costs view, darwin_cron_jobs table
│   └── seed.py                # Dev seed data
│
├── tests/
│   ├── conftest.py            # Shared fixtures — TestClient, mock pool
│   ├── test_finance.py        # Ledger invariants: debit, credit, overdraft rejection
│   ├── test_lifecycle.py      # Route existence and auth enforcement
│   ├── test_webhooks.py       # Stripe sig verification, webhook enqueue
│   └── test_security.py       # JWT realm separation, intent scan, critical invariants
│
├── pyproject.toml             # uv project — runtime deps + dev group
├── uv.lock                    # Pinned lockfile (committed)
├── Makefile                   # uv workflow shortcuts (`make help`)
├── Dockerfile                 # uv-based multi-stage build (Python 3.12-slim)
└── .env.example               # All required env vars documented
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) ≥ 0.4 — **all package management goes through uv, no pip**
- Python 3.12+
- A [Supabase](https://supabase.com) project with `pgcrypto`, `pgvector`, and Realtime enabled
- A Redis instance (local, Railway, or Upstash)
- A [Modal](https://modal.com) account

## Local setup

```bash
git clone <repo>
cd darwin-backend

# Install all dependencies (including dev group)
uv sync
# equivalent: make sync   or   make install

# Copy and fill in env vars
cp .env.example .env
```

## Makefile

The `Makefile` wraps the same `uv` commands documented below. From `darwin-backend/`, run **`make help`** for the full list. Common targets: `make install` / `make sync`, `make dev` (or `make run`), `make worker`, `make beat`, `make test`, `make lint`, `make format`, `make typecheck`, `make check` (lint + types + tests), `make clean`, `make docker-build`. Override **`PORT`**, **`HOST`**, or **`PYTEST_ARGS`** on the command line when needed (for example `make dev PORT=8080` or `make test PYTEST_ARGS="tests/test_finance.py -v"`).

Apply the database schema to your Supabase project:

```bash
# Option A — via Supabase CLI
supabase db push --db-url $DATABASE_URL db/migrations/001_initial.sql

# Option B — directly with psql
psql $DATABASE_URL -f db/migrations/001_initial.sql
```

## Running

Equivalent **`make`** targets are described in the [Makefile](#makefile) section.

**API server (development with hot reload):**

```bash
uv run uvicorn main:app --reload --port 8000
```

**Celery worker (processes webhook and wake queues):**

```bash
uv run celery -A workers.app worker --loglevel=info --concurrency=50 -P gevent
```

**Celery Beat scheduler (cron wake-ups + container health checks):**

```bash
uv run celery -A workers.app beat --loglevel=info
```

**Tests:**

```bash
uv run pytest
uv run pytest -v --tb=short          # verbose
uv run pytest tests/test_finance.py  # single file
```

**Linting / formatting:**

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
```

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_SECRET_KEY` | ✅ | Elevated server API key (`sb_secret_...` from Dashboard → API Keys). Same privilege class as the legacy JWT `service_role` key; Supabase accepts either until legacy keys are removed ([API keys](https://supabase.com/docs/guides/getting-started/api-keys)). Backend only — never expose to the frontend. |
| `SUPABASE_JWT_SECRET` | ✅ | Used to validate human JWTs from the frontend |
| `DATABASE_URL` | ✅ | asyncpg DSN — `postgresql://user:pass@host/db` |
| `AGENT_JWT_SECRET` | ✅ | Separate secret for agent JWTs — **never expose** |
| `VCC_ENCRYPTION_KEY` | ✅ | Symmetric passphrase for `pgp_sym_encrypt` (pgcrypto) |
| `MODAL_TOKEN_ID` | ✅ | Modal API token ID |
| `MODAL_TOKEN_SECRET` | ✅ | Modal API token secret |
| `MODAL_WEBHOOK_SECRET` | — | HMAC secret for Modal container lifecycle webhooks |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot API token |
| `TELEGRAM_ADMIN_CHAT_ID` | — | Chat ID for platform-level admin alerts |
| `STRIPE_WEBHOOK_SECRET` | ✅ | Stripe webhook signature secret (`whsec_...`) |
| `LANGFUSE_SECRET_KEY` | — | Langfuse secret key for Mental Replay trace fetching |
| `LANGFUSE_PUBLIC_KEY` | — | Langfuse public key |
| `LANGFUSE_HOST` | — | Langfuse base URL (default: `https://cloud.langfuse.com`) |
| `BROWSERBASE_API_KEY` | — | Browserbase API key for VCC injection relay |
| `BROWSERBASE_PROJECT_ID` | — | Browserbase project ID |
| `REDIS_URL` | ✅ | Redis connection string |
| `OPENAI_API_KEY` | — | OpenAI key for embeddings and intent scan fallback |
| `ANTHROPIC_API_KEY_1` | — | Anthropic key (gateway pool key 1) |
| `ANTHROPIC_API_KEY_2` | — | Anthropic key (gateway pool key 2) |
| `OPENAI_API_KEY_1` | — | OpenAI key (gateway pool key 1) |
| `OPENAI_API_KEY_2` | — | OpenAI key (gateway pool key 2) |
| `APP_BASE_URL` | — | Base URL for deep-link generation (default: `https://app.darwin.ai`) |
| `ENVIRONMENT` | — | `development` \| `staging` \| `production` |
| `CORS_ORIGINS` | — | JSON list of allowed origins |

## API overview

All routes are prefixed `/api/v1`. Docs available at `GET /docs` (disabled in production).

### Human-facing endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/users/register` | Create account (Supabase Auth proxy) |
| `POST` | `/users/login` | Sign in, receive JWT |
| `POST` | `/users/{uid}/vault` | Store encrypted VCC / API keys |
| `GET` | `/users/{uid}/swarm-state` | Aggregate P&L for the dashboard |
| `POST` | `/darwins/spawn` | Create and launch a new agent |
| `GET` | `/darwins/{id}/state` | FSM state + burn rate |
| `GET` | `/darwins/{id}/logs` | Langfuse thought log for Mental Replay |
| `POST` | `/darwins/{id}/wake` | Manually wake a hibernating agent |
| `POST` | `/darwins/{id}/terminate` | Kill switch |
| `POST` | `/darwins/{id}/uplink` | Inject mid-lifecycle prompt |
| `PATCH` | `/darwins/{id}/config` | Autonomy toggle + budget injection |
| `GET` | `/finance/ledger/{id}` | Balance + transaction history |
| `POST` | `/finance/withdraw` | ACH off-ramp via Stripe Connect |
| `GET` | `/telemetry/{id}/history` | Burn rate chart data |
| `GET` | `/identity/{id}/score` | Agent trust score |
| `POST` | `/firmware/upgrade` | Roll out new agent brain image |
| `POST` | `/firmware/migrate` | Queue SQLite schema migration |
| `GET` | `/audit/logs` | Compliance log viewer |
| `GET` | `/audit/export` | Tax report generation |
| `GET` | `/assets/{id}` | Download a generated artifact |

### Agent-facing endpoints (require agent JWT)

| Method | Path | Agent tool |
| --- | --- | --- |
| `POST` | `/telemetry/stream` | `post_llm_call` hook (rolling metrics) |
| `POST` | `/telemetry/stream-event` | all tools (canvas events) |
| `POST` | `/telemetry/llm-call` | `post_llm_call` hook (per-call cost record) |
| `POST` | `/telemetry/entity` | `save_entity()` |
| `POST` | `/telemetry/trip` | circuit breaker |
| `POST` | `/finance/ledger/update` | `check_ledger()`, `record_expense()` |
| `POST` | `/finance/inject-payment` | `inject_payment()` |
| `POST` | `/finance/microtx` | USDC settlement |
| `POST` | `/darwins/{id}/hibernate` | `hibernate()` |
| `POST` | `/darwins/{id}/cron` | Hermes `cronjob` tool |
| `DELETE` | `/darwins/{id}/cron/{name}` | Hermes `cronjob` tool |
| `GET` | `/darwins/{id}/cron` | Hermes `cronjob` tool |
| `POST` | `/memory/upload` | `save_skill()` |
| `POST` | `/memory/query` | `search_skills()` |
| `POST` | `/memory/royalty` | (automatic on skill use) |
| `GET` | `/gateway/limits` | `check_quota()` |
| `POST` | `/gateway/inference` | `llm_call()` |
| `POST` | `/audit/log` | `log_decision()` |
| `GET` | `/assets/presigned-url` | `upload_file()` |
| `POST` | `/assets/index` | `upload_file()` |
| `POST` | `/contracts/bounty` | `post_bounty()` |
| `GET` | `/contracts` | `browse_bounties()` |
| `POST` | `/contracts/{id}/lock` | `accept_contract()` |
| `POST` | `/contracts/{id}/settle` | `settle_contract()` |
| `POST` | `/notifications/send` | `ask_human()` |
| `POST` | `/notifications/session-link` | `captcha_delegate()` |

### Webhook endpoints (no auth — signature-verified)

| Method | Path | Source |
| --- | --- | --- |
| `POST` | `/webhooks/stripe` | Stripe |
| `POST` | `/webhooks/telegram` | Telegram Bot |
| `POST` | `/webhooks/email` | SendGrid / Postmark |
| `POST` | `/webhooks/modal` | Modal container events |

### WebSocket

| Path | Auth | Description |
| --- | --- | --- |
| `/sessions/{id}/stream` | `?token=<session_link_jwt>` | Live Browserbase relay for CAPTCHA solving |

## Database schema

11 tables, all with Row Level Security enabled:

| Table | Purpose |
| --- | --- |
| `user_profiles` | Extends `auth.users` with `telegram_chat_id` and `suspended_at` |
| `darwins` | Agent FSM state — the central state table |
| `vcc_vault` | `pgp_sym_encrypt` AES-256 encrypted PAN/CVV/expiry; raw card never stored in plaintext |
| `ledger_transactions` | Append-only immutable financial ledger (UPDATE/DELETE rules set to no-op) |
| `darwin_events` | Canonical event stream powering the Infinite Canvas; Supabase Realtime enabled |
| `hermes_entities` | SQLite entity mirror pushed from agent — accounts, clients, strategies, products |
| `hermes_skills` | Global skill document store with `VECTOR(1536)` embeddings (ivfflat index) |
| `hibernate_schedule` | CRON and WEBHOOK wake-up triggers registered by hibernating agents |
| `audit_log` | Append-only compliance record (UPDATE/DELETE rules set to no-op) |
| `contracts` | M2M bounty board — open, locked, settled, disputed |
| `assets` | Binary artifact index linked to Supabase Storage |

## Agent Finite State Machine

```
PENDING ──[spawn complete]──► ACTIVE
                                 │
               ┌─────────────────┤
               │                 │
         [hibernate()]      [terminate()]
               │                 │
               ▼                 ▼
         HIBERNATING         TERMINATED
               │
        [wake trigger]
               │
               └──────────► ACTIVE
```

Invalid FSM transitions return `409 Conflict` with `{ "error": "invalid_state_transition", "current_state": "..." }`.

`TERMINATED` is permanent — no transitions out.

## Security invariants

The following invariants are enforced at the infrastructure level, not just in software:

1. **An agent JWT cannot access `/users` endpoints** — the two JWT secrets are entirely different; no token signed with one validates against the other.
2. **An agent cannot read another agent's ledger** — PostgreSQL RLS blocks it before Python code runs.
3. **A debit that would result in negative balance is always rejected** — `LedgerService.apply_transaction()` holds a `SELECT … FOR UPDATE` row lock; `InsufficientFundsError` is raised before any mutation.
4. **Raw PAN never appears in logs, response bodies, or error messages** — `VCCService` decrypts via SQL (`pgp_sym_decrypt`), types the card directly into Browserbase DOM, and then immediately zeroes + deletes the plaintext variables.
5. **Every `darwin_events` insert triggers Supabase Realtime** — tested in the integration suite with a live Realtime subscription.

## Deployment

Three Railway services from the same Docker image:

```
# darwin-backend (API server)
Start: uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Health: GET /health → 200

# darwin-celery-worker
Start: uv run celery -A workers.app worker --loglevel=info --concurrency=50 -P gevent
Queues: webhooks, wake, telemetry

# darwin-celery-beat (cron scheduler)
Start: uv run celery -A workers.app beat --loglevel=info
Tasks: check_due_wakeups (30s), check_container_health (60s)
```

The backend must be a long-running process — it must never be serverless. It maintains persistent database connections, Redis connections, and serves WebSocket relay for the CAPTCHA flow.

## Adding a router

1. Create `routers/new_router.py` with `router = APIRouter(prefix="/new", tags=["new"])`.
2. Add any new Pydantic schemas to `schemas/`.
3. Import and register the router in `main.py` with `app.include_router(new_router.router, prefix=settings.api_v1_prefix)`.

## Adding a Celery task

1. Define the task in the appropriate `workers/*.py` file with `@celery_app.task(...)`.
2. Add the module to `celery_app.conf.include` in `workers/app.py` if it's a new file.
3. For Beat tasks, add an entry to `celery_app.conf.beat_schedule`.
