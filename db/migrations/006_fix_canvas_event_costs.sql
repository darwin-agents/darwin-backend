-- Migration 004: Fix darwin_events cost column precision
--
-- darwin_events.token_cost was NUMERIC(8,6) — max 99.999999.
-- pnl_hooks passes raw token counts (e.g. 15 000+), overflowing the column
-- and raising asyncpg NumericValueOutOfRangeError → unhandled 500 on every
-- /telemetry/stream-event call.
--
-- Fix: token_cost → BIGINT (it's a raw token count, never fractional)
--       fiat_cost  → NUMERIC(14,6) (supports up to $99 999 999.999999/event)

ALTER TABLE darwin_events
  ALTER COLUMN token_cost TYPE BIGINT
    USING ROUND(token_cost)::BIGINT,
  ALTER COLUMN fiat_cost  TYPE NUMERIC(14,6);

-- Also sync the CHECK on event_type to match the application's VALID_EVENT_TYPES.
-- New types added after the initial migration are rejected by the DB constraint
-- and would also cause CheckViolationError on INSERT.
ALTER TABLE darwin_events
  DROP CONSTRAINT IF EXISTS darwin_events_event_type_check;

ALTER TABLE darwin_events
  ADD CONSTRAINT darwin_events_event_type_check CHECK (event_type IN (
    -- Lifecycle
    'AGENT_SPAWNED','AGENT_WOKE','AGENT_HIBERNATED','AGENT_TERMINATED',
    'AGENT_KILLED','SESSION_ENDED',
    -- Cognitive
    'THOUGHT','LLM_INFERENCE',
    -- Tools
    'TOOL_CALL','TOOL_RESULT',
    -- Sub-agents / neurons
    'NEURON_SPAWNED','NEURON_COMPLETED','NEURON_FAILED','SYNAPTIC_INTEGRATION',
    'DELEGATE_SPAWNED','DELEGATE_COMPLETED',
    -- Code execution
    'CODE_EXEC_START','CODE_EXEC_END',
    -- Browser & vision
    'BROWSER_FETCH','BROWSER_SESSION_OPEN','BROWSER_SCREENSHOT',
    'BROWSER_CLICK','BROWSER_TYPE','BROWSER_SESSION_CLOSE',
    -- Finance
    'LEDGER_DEBIT','LEDGER_CREDIT','VCC_INJECTED','USDC_TX',
    -- Human-in-the-loop
    'ASK_HUMAN_SENT','ASK_HUMAN_RESOLVED',
    'APPROVAL_REQUESTED','APPROVAL_GRANTED','APPROVAL_DENIED',
    'CAPTCHA_DELEGATED','CAPTCHA_SOLVED',
    -- Memory
    'SKILL_CREATED','SKILL_LOADED','SKILL_EXECUTED',
    'EPISODIC_SUMMARY_WRITTEN','MEMORY_COMPRESSED',
    'ENTITY_CREATED','ENTITY_UPDATED',
    -- Safety & telemetry
    'CIRCUIT_BREAKER_TRIPPED','PROVIDER_ROTATED',
    -- Scheduling
    'CRON_SCHEDULED','CRON_CANCELLED','CRON_FIRED',
    -- Control plane
    'UPLINK_INJECTED','CONFIG_UPDATED'
  ));
