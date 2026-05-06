-- Migration 005: Add ON DELETE CASCADE to ledger_transactions.darwin_id FK
--
-- Allows deleting a darwin to cascade-remove its ledger rows.
-- Also drops the old no_delete_ledger rule which blocked ALL deletes
-- (including cascades), then re-creates update-only protection.

ALTER TABLE ledger_transactions
  DROP CONSTRAINT IF EXISTS ledger_transactions_darwin_id_fkey;

ALTER TABLE ledger_transactions
  ADD CONSTRAINT ledger_transactions_darwin_id_fkey
    FOREIGN KEY (darwin_id) REFERENCES darwins(id) ON DELETE CASCADE;

-- Remove the broad no-delete rule; cascade deletes must be allowed.
DROP RULE IF EXISTS no_delete_ledger ON ledger_transactions;

-- Update protection is preserved via no_update_ledger (defined in 001_initial.sql).
