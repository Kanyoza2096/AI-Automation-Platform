-- Provider reconciliation contract.
-- Reconciliation is intentionally conservative: an uncertain external action
-- may only be auto-resolved from authoritative provider evidence.

ALTER TABLE publish_outbox
  ADD COLUMN IF NOT EXISTS reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS reconciliation_error TEXT,
  ADD COLUMN IF NOT EXISTS consecutive_not_found INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_publish_outbox_uncertain_reconcile
  ON publish_outbox(status, last_reconciled_at)
  WHERE status = 'uncertain';
