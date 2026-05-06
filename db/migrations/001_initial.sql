-- Darwin Backend — Phase 1 Initial Schema
-- Run via: supabase db push  OR  psql $DATABASE_URL -f 001_initial.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- Extensions
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. user_profiles  (extends Supabase auth.users)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profiles (
  id                UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_chat_id  BIGINT,
  suspended_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own profile" ON user_profiles
  FOR ALL USING (auth.uid() = id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. darwins  (one row per agent — the FSM state table)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS darwins (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               UUID NOT NULL REFERENCES auth.users(id),
  name                  TEXT NOT NULL,
  state                 TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (state IN ('PENDING','ACTIVE','HIBERNATING','TERMINATED')),

  -- Genetics
  system_prompt         TEXT NOT NULL,
  llm_model             TEXT NOT NULL,
  tool_set              JSONB NOT NULL DEFAULT '[]',

  -- Financial
  vcc_ref               TEXT,
  ledger_balance        NUMERIC(12,4) NOT NULL DEFAULT 0,
  initial_budget        NUMERIC(12,4) NOT NULL,

  -- Modal
  modal_function_name   TEXT,
  modal_volume_id       TEXT,
  modal_call_id         TEXT,   -- last spawned container call_id

  -- Autonomy
  requires_approval     BOOLEAN DEFAULT TRUE,

  -- Timing
  spawned_at            TIMESTAMPTZ,
  last_active_at        TIMESTAMPTZ,
  terminated_at         TIMESTAMPTZ,
  created_at            TIMESTAMPTZ DEFAULT now(),

  -- Telemetry summary
  total_tokens_burned   BIGINT DEFAULT 0,
  total_compute_cost    NUMERIC(10,4) DEFAULT 0,
  total_revenue         NUMERIC(12,4) DEFAULT 0,
  token_roi             NUMERIC(8,4)
);

ALTER TABLE darwins ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own darwins" ON darwins
  FOR ALL USING (auth.uid() = user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. vcc_vault  (encrypted card storage — pgcrypto)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vcc_vault (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id         UUID NOT NULL REFERENCES darwins(id) ON DELETE CASCADE,
  user_id           UUID NOT NULL REFERENCES auth.users(id),
  encrypted_pan     BYTEA NOT NULL,
  encrypted_cvv     BYTEA NOT NULL,
  encrypted_expiry  BYTEA NOT NULL,
  spend_limit       NUMERIC(10,2) NOT NULL,
  provider          TEXT NOT NULL DEFAULT 'manual',
  created_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE vcc_vault ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own vccs" ON vcc_vault
  FOR ALL USING (auth.uid() = user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. ledger_transactions  (append-only immutable log)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ledger_transactions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id     UUID NOT NULL REFERENCES darwins(id) ON DELETE CASCADE,
  amount        NUMERIC(12,4) NOT NULL,
  balance_after NUMERIC(12,4) NOT NULL,
  type          TEXT NOT NULL CHECK (type IN (
    'INITIAL_FUND','VCC_DEBIT','REVENUE_CREDIT',
    'ROYALTY_DEBIT','ROYALTY_CREDIT','ESCROW_LOCK',
    'ESCROW_RELEASE','METABOLIC_INJECTION','WITHDRAWAL'
  )),
  description   TEXT,
  external_ref  TEXT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE ledger_transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own transactions" ON ledger_transactions
  FOR ALL USING (
    darwin_id IN (SELECT id FROM darwins WHERE user_id = auth.uid())
  );

-- Append-only: block in-row UPDATEs. Rows may be removed when the parent darwin is deleted (FK CASCADE).
CREATE RULE no_update_ledger AS ON UPDATE TO ledger_transactions DO INSTEAD NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. darwin_events  (the Infinite Canvas event stream)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS darwin_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id       UUID NOT NULL REFERENCES darwins(id) ON DELETE CASCADE,
  session_id      UUID NOT NULL,
  parent_event_id UUID REFERENCES darwin_events(id) ON DELETE CASCADE,
  neuron_id       TEXT,

  event_type      TEXT NOT NULL CHECK (event_type IN (
    'AGENT_SPAWNED','AGENT_WOKE','AGENT_HIBERNATED','AGENT_TERMINATED',
    'THOUGHT','TOOL_CALL','TOOL_RESULT','LLM_INFERENCE',
    'NEURON_SPAWNED','NEURON_COMPLETED','NEURON_FAILED','SYNAPTIC_INTEGRATION',
    'BROWSER_FETCH','BROWSER_SESSION_OPEN','BROWSER_SCREENSHOT',
    'BROWSER_CLICK','BROWSER_TYPE','BROWSER_SESSION_CLOSE',
    'LEDGER_DEBIT','LEDGER_CREDIT','VCC_INJECTED','USDC_TX',
    'ASK_HUMAN_SENT','ASK_HUMAN_RESOLVED',
    'APPROVAL_REQUESTED','APPROVAL_GRANTED','APPROVAL_DENIED',
    'CAPTCHA_DELEGATED','CAPTCHA_SOLVED',
    'SKILL_CREATED','SKILL_LOADED','EPISODIC_SUMMARY_WRITTEN',
    'ENTITY_CREATED','ENTITY_UPDATED',
    'CIRCUIT_BREAKER_TRIPPED',
    'UPLINK_INJECTED','CONFIG_UPDATED'
  )),

  payload         JSONB NOT NULL DEFAULT '{}',
  timestamp_ms    BIGINT NOT NULL,
  token_cost      NUMERIC(8,6) DEFAULT 0,
  fiat_cost       NUMERIC(8,6) DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_darwin_events_darwin_session
  ON darwin_events(darwin_id, session_id);
CREATE INDEX IF NOT EXISTS idx_darwin_events_neuron
  ON darwin_events(darwin_id, neuron_id);
CREATE INDEX IF NOT EXISTS idx_darwin_events_type
  ON darwin_events(darwin_id, event_type);
CREATE INDEX IF NOT EXISTS idx_darwin_events_timestamp
  ON darwin_events(darwin_id, timestamp_ms DESC);

ALTER TABLE darwin_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own events" ON darwin_events
  FOR ALL USING (
    darwin_id IN (SELECT id FROM darwins WHERE user_id = auth.uid())
  );

-- Enable Supabase Realtime on this table
ALTER PUBLICATION supabase_realtime ADD TABLE darwin_events;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. hermes_entities  (SQLite mirror from agent)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hermes_entities (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id    UUID NOT NULL REFERENCES darwins(id),
  session_id   UUID NOT NULL,
  entity_type  TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  data         JSONB NOT NULL,
  is_deleted   BOOLEAN DEFAULT FALSE,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE(darwin_id, entity_type, entity_id)
);

ALTER TABLE hermes_entities ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own entities" ON hermes_entities
  FOR ALL USING (
    darwin_id IN (SELECT id FROM darwins WHERE user_id = auth.uid())
  );

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. hermes_skills  (global skill doc vector store)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hermes_skills (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author_darwin_id UUID REFERENCES darwins(id),
  title            TEXT NOT NULL,
  content          TEXT NOT NULL,
  embedding        VECTOR(1536),
  usage_count      INTEGER DEFAULT 0,
  royalty_usdc     NUMERIC(8,6) DEFAULT 0.02,
  created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hermes_skills_embedding
  ON hermes_skills USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. hibernate_schedule  (wake-up triggers)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hibernate_schedule (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id              UUID NOT NULL REFERENCES darwins(id) ON DELETE CASCADE,
  trigger_type           TEXT NOT NULL CHECK (trigger_type IN ('CRON','WEBHOOK')),
  trigger_at             TIMESTAMPTZ,
  webhook_key            TEXT UNIQUE,
  wake_prompt            TEXT NOT NULL,
  browserbase_session_id TEXT,
  created_at             TIMESTAMPTZ DEFAULT now(),
  fired_at               TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hibernate_schedule_trigger
  ON hibernate_schedule(trigger_at) WHERE fired_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. audit_log  (append-only compliance record)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id        UUID NOT NULL REFERENCES darwins(id),
  event_type       TEXT NOT NULL,
  prompt_snapshot  TEXT,
  context_snapshot JSONB,
  decision         TEXT,
  financial_impact NUMERIC(12,4),
  created_at       TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own audit" ON audit_log
  FOR SELECT USING (
    darwin_id IN (SELECT id FROM darwins WHERE user_id = auth.uid())
  );

CREATE RULE no_update_audit AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE no_delete_audit AS ON DELETE TO audit_log DO INSTEAD NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. contracts  (M2M bounty board)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contracts (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  poster_darwin_id UUID NOT NULL REFERENCES darwins(id),
  taker_darwin_id  UUID REFERENCES darwins(id),
  task_description TEXT NOT NULL,
  reward_usdc      NUMERIC(10,6) NOT NULL,
  state            TEXT NOT NULL DEFAULT 'OPEN'
                     CHECK (state IN ('OPEN','LOCKED','DELIVERED','SETTLED','DISPUTED')),
  delivery_payload JSONB,
  timeout_at       TIMESTAMPTZ NOT NULL,
  created_at       TIMESTAMPTZ DEFAULT now(),
  settled_at       TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. assets  (binary artifact index)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darwin_id    UUID NOT NULL REFERENCES darwins(id),
  filename     TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes   BIGINT,
  storage_path TEXT NOT NULL,
  description  TEXT,
  created_at   TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own assets" ON assets
  FOR ALL USING (
    darwin_id IN (SELECT id FROM darwins WHERE user_id = auth.uid())
  );
