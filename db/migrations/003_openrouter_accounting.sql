-- Migration 003: OpenRouter-era accounting — LLM_USAGE_DEBIT, idempotent refs, multimodal telemetry, rollups.

-- Allow LLM_USAGE_DEBIT in the ledger (append-only; CHECK must replace prior list).
ALTER TABLE ledger_transactions DROP CONSTRAINT IF EXISTS ledger_transactions_type_check;

ALTER TABLE ledger_transactions ADD CONSTRAINT ledger_transactions_type_check
  CHECK (type IN (
    'INITIAL_FUND','VCC_DEBIT','REVENUE_CREDIT',
    'ROYALTY_DEBIT','ROYALTY_CREDIT','ESCROW_LOCK',
    'ESCROW_RELEASE','METABOLIC_INJECTION','WITHDRAWAL','LLM_USAGE_DEBIT'
  ));

-- One ledger debit per inferred LLM call id (telemetry idempotency).
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_llm_usage_external_ref
  ON ledger_transactions (darwin_id, external_ref)
  WHERE type = 'LLM_USAGE_DEBIT' AND external_ref IS NOT NULL;

ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS input_modalities JSONB DEFAULT '[]'::JSONB;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS output_modalities JSONB DEFAULT '[]'::JSONB;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS image_output_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS audio_output_seconds NUMERIC(12, 4) NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS video_output_seconds NUMERIC(12, 4) NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_calls_darwin_request_id
  ON llm_calls (darwin_id, request_id)
  WHERE request_id IS NOT NULL;

ALTER TABLE llm_calls ALTER COLUMN provider SET DEFAULT 'openrouter';

-- Daily rollups for dashboards / budget analytics.
CREATE TABLE IF NOT EXISTS darwin_spend_daily (
  darwin_id UUID NOT NULL REFERENCES darwins(id) ON DELETE CASCADE,
  day DATE NOT NULL,
  llm_cost_usd NUMERIC(14, 8) NOT NULL DEFAULT 0,
  token_count BIGINT NOT NULL DEFAULT 0,
  call_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (darwin_id, day)
);

CREATE INDEX IF NOT EXISTS idx_darwin_spend_daily_day ON darwin_spend_daily (day DESC);

CREATE OR REPLACE VIEW darwin_spend_monthly AS
SELECT
  darwin_id,
  date_trunc('month', day::timestamp)::date AS month_start,
  SUM(llm_cost_usd) AS total_llm_cost_usd,
  SUM(token_count) AS total_tokens,
  SUM(call_count) AS total_calls
FROM darwin_spend_daily
GROUP BY darwin_id, date_trunc('month', day::timestamp)::date;
