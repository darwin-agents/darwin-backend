-- Migration 002: Hermes capabilities additions
-- Adds tables for LLM call tracking, cron scheduling.

-- Per-LLM-call cost log for Token ROI dashboard and Mental Replay overlay.
CREATE TABLE IF NOT EXISTS llm_calls (
    id              BIGSERIAL PRIMARY KEY,
    darwin_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    model           TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'anthropic',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 8) NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    turn_id         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_darwin_session
    ON llm_calls (darwin_id, session_id, created_at DESC);

-- Per-session cost roll-up view (powers the Token ROI panel)
CREATE OR REPLACE VIEW session_costs AS
SELECT
    darwin_id,
    session_id,
    SUM(input_tokens)     AS total_input_tokens,
    SUM(output_tokens)    AS total_output_tokens,
    SUM(cost_usd)         AS total_cost_usd,
    COUNT(*)              AS llm_call_count,
    MIN(created_at)       AS started_at,
    MAX(created_at)       AS last_call_at
FROM llm_calls
GROUP BY darwin_id, session_id;

-- Cron jobs scheduled by the agent via schedule_cron() tool.
CREATE TABLE IF NOT EXISTS darwin_cron_jobs (
    id          BIGSERIAL PRIMARY KEY,
    darwin_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    cron_expr   TEXT NOT NULL,
    wake_prompt TEXT NOT NULL DEFAULT 'Scheduled check-in.',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    next_run_at TIMESTAMPTZ,
    last_fired_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (darwin_id, name)
);

CREATE INDEX IF NOT EXISTS idx_cron_jobs_next_run
    ON darwin_cron_jobs (next_run_at)
    WHERE enabled = TRUE;
