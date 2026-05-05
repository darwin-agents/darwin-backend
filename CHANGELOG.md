# Changelog

All notable changes to `darwin-backend` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **Fly.io deployment scaffolding** — added `fly.toml` (Machines `http_service`, `/health` checks, FastAPI process command) and `.dockerignore` tuned for smaller, reproducible Docker builds on Fly.

### Changed

- **README deployment docs** — added a full Fly.io deployment path (`fly apps create`, `fly secrets set`, `fly deploy`, checks/log verification) aligned with current Fly docs for FastAPI and service-level health checks.
- **Fly test-cost profile:** `fly.toml` now uses `shared-cpu-1x` + `256mb`, one Uvicorn worker, and scale-to-zero idle settings (`auto_stop_machines = "stop"`, `min_machines_running = 0`) for minimal solo testing cost.

---

## [0.2.0] — 2026-05-05

### Added

- **Supabase human JWT** — `get_current_user` verifies **RS256 / ES256** (and related) access tokens using the project **JWKS** (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), with a short-lived in-memory cache. **HS256** tokens still use `SUPABASE_JWT_SECRET` as before.
- **OpenRouter-only runtime** — `POST /gateway/inference`, intent scan, and skill embeddings all call **OpenRouter** (`OPENROUTER_API_KEY_*`, `OPENROUTER_BASE_URL`). Direct Anthropic/OpenAI vendor endpoints removed from the backend.
- **LLM cost accounting** — `POST /telemetry/llm-call` now records `request_id`, optional multimodal columns, bumps **`darwin_spend_daily`**, writes an idempotent **`LLM_USAGE_DEBIT`** ledger row (`external_ref`: `llm:<request_id>`), and supports **`SESSION_LLM_SPEND_CAP_USD`** enforced via Redis. Schema: `db/migrations/003_openrouter_accounting.sql`.
- **`services/openrouter.py`** — shared OpenAI-compatible helpers for chat + embeddings responses.

### Changed

- **Breaking (env):** replace `OPENAI_*` / `ANTHROPIC_*` keys with `OPENROUTER_API_KEY_1` (+ optional `_2`). See `.env.example` and README.
- **Intent scan** — `IntentScanService.scan_directive` routes through OpenRouter (`OPENROUTER_INTENT_SCAN_MODEL`); logs a warning and **fails open** on transport/API failures so `/darwins/spawn` is never hard-failed due to outages.
- **Spawn stability:** `ModalService.spawn_container` now uses `modal.Function.from_name(...)` (compatible with current Modal SDK), fixing spawn failures from removed `Function.lookup`, and **`await fn.spawn.aio(...)`** so Modal is not invoked via blocking APIs inside async routes.
- **Modal auth bootstrap:** `ModalService` now injects `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` from backend settings into process env before invoking the Modal SDK, preventing local spawn failures when CLI token state is missing.
- **Modal target configurability:** backend can now select Modal app/function/environment via `MODAL_APP_NAME`, `MODAL_FUNCTION_NAME`, `MODAL_ENVIRONMENT_NAME` (instead of hardcoded `darwin-agent` + `run_agent` on default env), fixing lookup failures when deployed outside `main`.
- **Frontend compatibility routes:** added `GET /darwins/?owner_id=...` and `GET /darwins/{id}/events` (supports `limit` and `order=timestamp_ms.asc|desc`) to backfill canvas/list queries previously returning 404.
- **Intent scan resilience:** classifier now retries a small fallback model list when OpenRouter returns `400` for unavailable/invalid model IDs before failing open.
- **Supabase PgBouncer compatibility:** `db/client.py` now disables asyncpg statement caching automatically when `DATABASE_URL` points to `*.pooler.supabase.com`, preventing `DuplicatePreparedStatementError` on spawn and other write paths.
- **Breaking:** `SUPABASE_SERVICE_ROLE_KEY` removed. Use `SUPABASE_SECRET_KEY` only — set it to your project’s **secret** API key (`sb_secret_...`) or, during migration, the legacy JWT `service_role` value (Supabase treats both as elevated `apikey` credentials). `get_supabase()` now initializes the client with `supabase_secret_key`.
- **Breaking (env):** Mental Replay trace fetching now uses **Langfuse** instead of LangSmith. Replace `LANGSMITH_API_KEY` and `LANGCHAIN_PROJECT` with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and optional `LANGFUSE_HOST` (default `https://cloud.langfuse.com`). `GET /darwins/{id}/logs` queries `GET /api/public/traces` using Langfuse session id `{darwin_id}::{session_id}`, matching Hermes `langfuse.session_name` in `darwin-agent/config/config.yaml.j2`. Implementation: `services/langfuse_service.py` (removed `langsmith_service.py`).

### Planned

- `schemas/telemetry.py` — update `VALID_EVENT_TYPES` as new canvas event types are discovered.

---

## [0.1.1] — 2026-05-02

Hermes capabilities additions — new endpoints and schema to support the v0.1 agent architecture.

### Added

**Telemetry**

- `POST /telemetry/llm-call` — records a single LLM API call with full token breakdown (`input_tokens`, `output_tokens`, `cache_read_tokens`, `reasoning_tokens`, `cost_usd`, `duration_ms`, `provider`, `turn_id`). Fires a `LLM_INFERENCE` canvas event for the Mental Replay overlay. Called by the agent's `post_llm_call` hook after every inference.
- `schemas/telemetry.py` — added `LLMCallRequest` Pydantic model
- `schemas/telemetry.py` — `VALID_EVENT_TYPES` expanded by 18 new types: `AGENT_KILLED`, `SESSION_ENDED`, `LLM_INFERENCE`, `DELEGATE_SPAWNED`, `DELEGATE_COMPLETED`, `CODE_EXEC_START`, `CODE_EXEC_END`, `SKILL_EXECUTED`, `MEMORY_COMPRESSED`, `PROVIDER_ROTATED`, `CRON_SCHEDULED`, `CRON_CANCELLED`, `CRON_FIRED`

**Cron scheduling**

- `POST /darwins/{id}/cron` — agent creates a recurring wake-up schedule. Validates the cron expression via `croniter`, stores in `darwin_cron_jobs`, returns `{ cron_id, next_run_utc }`. Called by the Hermes built-in `cronjob` tool wired to the Darwin God Layer.
- `DELETE /darwins/{id}/cron/{name}` — agent cancels a named cron job
- `GET /darwins/{id}/cron` — agent lists its active cron jobs

**Database**

- `db/migrations/002_hermes_capabilities.sql`:
  - `llm_calls` table — per-call cost log for Token ROI dashboard
  - `session_costs` view — aggregate cost roll-up per session (total tokens, total USD, call count)
  - `darwin_cron_jobs` table — agent-scheduled recurring wake-ups with `next_run_at` index

### Changed

- `pyproject.toml` — added `croniter>=2.0.0` dependency (cron expression validation)

---

## [0.1.0] — 2026-05-02

Initial implementation of the Phase 1 MVP spec (`darwin-docs/mvp/2_darwin-backend.md`).

### Added

**Application entry point**

- `main.py` — FastAPI app factory with `lifespan` context (asyncpg pool warm-up on startup, graceful drain on shutdown), global `500` exception handler with `trace_id`, CORS middleware, and all 15 routers registered under `/api/v1`
- `GET /health` — liveness probe (used by Railway health checks)

**Configuration**

- `config.py` — `pydantic-settings` `Settings` model reading all env vars from `.env`; `get_settings()` cached with `@lru_cache`; `get_anthropic_keys()` / `get_openai_keys()` helpers for gateway key pool

**Dependency injection** (`deps.py`)

- `get_current_user` — validates Supabase-issued human JWTs against `SUPABASE_JWT_SECRET`
- `get_current_agent` — validates short-lived agent JWTs against `AGENT_JWT_SECRET`; rejects any token not typed `"agent"`
- `mint_agent_jwt` — mints 2-hour agent JWT injected into Modal container env at spawn
- `mint_session_link_jwt` / `decode_session_link_jwt` — time-limited JWT for CAPTCHA / 2FA mobile deep-links

**Database layer** (`db/`)

- `client.py` — `asyncpg` connection pool (`min=2`, `max=20`, `25s` statement timeout) + Supabase async client; `close_all()` on shutdown
- `migrations/001_initial.sql` — full Phase 1 schema:
  - Extensions: `pgcrypto`, `vector`
  - Tables: `user_profiles`, `darwins`, `vcc_vault`, `ledger_transactions`, `darwin_events`, `hermes_entities`, `hermes_skills`, `hibernate_schedule`, `audit_log`, `contracts`, `assets`
  - Row Level Security policies on every table (users see only their own rows)
  - Append-only rules (`ON UPDATE/DELETE DO INSTEAD NOTHING`) on `ledger_transactions` and `audit_log`
  - `ivfflat` vector index on `hermes_skills.embedding` for cosine similarity search
  - `ALTER PUBLICATION supabase_realtime ADD TABLE darwin_events` for Infinite Canvas fan-out

**Pydantic schemas** (`schemas/`)

- `agents.py` — `SpawnRequest`, `HibernateRequest`, `UplinkRequest`, `ConfigPatchRequest`, `AgentStateResponse`, `AgentSummary`, `SwarmStateResponse`
- `finance.py` — `VCCPayload` with Luhn algorithm validator, `VaultSubmitRequest`, `LedgerUpdateRequest`, `LedgerUpdateResult`, `InjectPaymentRequest`, `LedgerResponse`, `MicroTxRequest`, `WithdrawRequest`
- `telemetry.py` — `TelemetryStreamRequest`, `CanvasEventRequest` (validated against `VALID_EVENT_TYPES` frozenset), `EntityPushRequest`, `CircuitBreakerTripRequest`
- `canvas.py` — typed payload shapes for all 35 `darwin_events` event types used by the frontend Infinite Canvas
- `hermes.py` — `HermesEntityRecord`, `HermesSkillRecord` mirror types
- `memory.py` — `SkillUploadRequest`, `SkillQueryRequest`, `SkillQueryResult`, `RoyaltyRequest`

**Services** (`services/`)

- `ledger_service.py` — `LedgerService.apply_transaction()` with `SELECT … FOR UPDATE` row-level locking; raises `InsufficientFundsError` on overdraft; fire-and-forget canvas event push; low-balance Telegram alert at 20% of initial budget
- `vcc_service.py` — `VCCService.store_vcc()` encrypts PAN/CVV/expiry via `pgp_sym_encrypt` (SQL-side, never plaintext in Python); `decrypt_and_inject()` decrypts JIT → types into Browserbase DOM → immediately zeroes and deletes plaintext variables; `verify_ownership()` for agent authorization check
- `canvas_service.py` — `CanvasService.push_event()` inserts into `darwin_events`; Supabase Realtime handles fan-out automatically; convenience helpers `push_neuron_spawned()`, `push_synaptic_integration()`, `push_lifecycle()`
- `modal_service.py` — `ModalService.spawn_container()` via Modal SDK `fn.spawn()`; `kill_container()` via Redis `kill:{darwin_id}` key (agent's heartbeat loop polls this); `get_container_status()` via Redis `heartbeat:{darwin_id}` key; `publish_uplink()` via Redis pub/sub channel; `update_image_tag()` / `get_image_tag()` for firmware upgrades
- `telegram_service.py` — `TelegramService` wrapping Telegram Bot API; `send_alert_by_darwin_id()` (resolves `telegram_chat_id` via DB join); `send_approval_request()` with `[✅ Approve]` / `[❌ Deny]` inline keyboard; `send_captcha_link()` with deep-link; `send_admin_alert()` for platform-level notifications
- `intent_scan_service.py` — `IntentScanService.scan_directive()` calls Claude Haiku with a strict security classification system prompt; returns `(flagged: bool, reason: str | None)`; fails open (returns `False`) if no API key is configured
- `langsmith_service.py` — `LangSmithService.get_trace()` fetches LangSmith runs filtered by `session_id` tag; 5-minute Redis cache per session; maps run types to canvas event types

**Celery workers** (`workers/`)

- `app.py` — Celery app with `gevent` pool, three named queues (`webhooks`, `wake`, `telemetry`), Beat schedule for `check_due_wakeups` (30s) and `check_container_health` (60s)
- `webhook_worker.py` — `process_stripe_webhook`: extracts `darwin_id` from Stripe metadata, credits ledger, triggers wake; `process_telegram_webhook`: parses callback_query or message, resolves `webhook_key`, constructs wake prompt; `process_email_webhook`: parses inbound email, wakes target agent; all tasks have `max_retries=3`
- `wake_worker.py` — `execute_agent_wake`: loads hibernating agent, mints fresh agent JWT, calls `modal_service.spawn_container()`, updates state to `ACTIVE`, marks `hibernate_schedule` row as fired, emits `AGENT_WOKE` canvas event, sends Telegram notification; `check_due_wakeups`: Beat task querying `hibernate_schedule` for elapsed CRON triggers
- `telemetry_worker.py` — `persist_telemetry_batch`: stores metabolic samples in Redis time-series (1000-sample ring buffer, 48h TTL), updates `darwins` cumulative totals, evaluates circuit-breaker thresholds (5× historical burn rate, 10 failed HTTP requests in 30s); `_trip()`: terminates container, emits `CIRCUIT_BREAKER_TRIPPED` event, sends Telegram alert; `check_container_health`: Beat task detecting containers that crashed without hibernating, auto-schedules 5-minute CRON resume

**Routers** (`routers/`) — 15 routers, all endpoints listed in README

| Router          | Key endpoints                                                                        | Auth             |
| --------------- | ------------------------------------------------------------------------------------ | ---------------- |
| `users`         | register, login, vault, swarm-state                                                  | Human JWT        |
| `darwins`       | spawn (with intent scan), state, logs, hibernate, wake, terminate, uplink, config    | Human + Agent    |
| `finance`       | mint-card, ledger/update (FOR UPDATE), inject-payment, microtx, ledger GET, withdraw | Human + Agent    |
| `webhooks`      | stripe (sig verify), telegram, email, modal                                          | Signature        |
| `telemetry`     | stream, stream-event, entity, trip, history                                          | Agent            |
| `memory`        | upload (embed + pgvector), query (cosine similarity), royalty                        | Agent            |
| `contracts`     | bounty, list, lock (escrow), settle (release/dispute)                                | Agent            |
| `assets`        | presigned-url, index, get                                                            | Human + Agent    |
| `audit`         | log, logs, export (tax report)                                                       | Human + Agent    |
| `identity`      | score (aggregated from contracts), dispute                                           | Human + Agent    |
| `gateway`       | inference (key-pool round-robin), limits (API weather)                               | Agent            |
| `firmware`      | upgrade (Redis image tag), migrate (Redis migration script)                          | Human            |
| `notifications` | send, session-link, resolve (wake on human response), captcha-session                | Agent            |
| `sessions`      | `WS /stream` (Browserbase relay + resume signal)                                     | Session-link JWT |
| `security`      | intent-scan, suspend-account, unsuspend-account                                      | Internal + Human |

**Package management**

- `pyproject.toml` — uv project with `[dependency-groups] dev` (replaces `[project.optional-dependencies]`), `[tool.uv] package = false`
- `uv.lock` — pinned lockfile (130 packages, committed)
- `Dockerfile` — copies uv binary from `ghcr.io/astral-sh/uv:latest`; `uv sync --frozen --no-dev` for production layer; `requirements.txt` removed

**Tests** (`tests/`)

- `conftest.py` — session-scoped `TestClient` with mocked DB pool; env vars pre-seeded for import-time Settings validation
- `test_finance.py` — `LedgerService` unit tests: credit, debit within balance, overdraft rejection (critical invariant), zero-balance rejection
- `test_lifecycle.py` — route existence, auth enforcement on all major endpoints
- `test_webhooks.py` — Stripe signature verification rejection, email/Telegram webhook enqueue
- `test_security.py` — agent JWT cannot access human routes, intent scan flagging, critical security invariants from spec §19.3

[Unreleased]: https://github.com/darwin-agents/darwin-backend/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/darwin-agents/darwin-backend/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/darwin-agents/darwin-backend/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/darwin-agents/darwin-backend/releases/tag/v0.1.0
