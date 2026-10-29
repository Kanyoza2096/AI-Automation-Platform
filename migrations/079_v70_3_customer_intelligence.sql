-- V70.3 Customer Intelligence & Relationship Context
CREATE TABLE IF NOT EXISTS customer_relationship_context (
  workspace_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL DEFAULT 'unknown',
  interaction_count BIGINT NOT NULL DEFAULT 0,
  positive_signals DOUBLE PRECISION NOT NULL DEFAULT 0,
  negative_signals DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_signal_type TEXT NOT NULL DEFAULT '',
  last_signal_source TEXT NOT NULL DEFAULT '',
  recommended_workflow TEXT,
  recommendation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  context_fingerprint TEXT NOT NULL,
  authority_granted BOOLEAN NOT NULL DEFAULT FALSE,
  execution_effect TEXT NOT NULL DEFAULT 'none',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, customer_id),
  CONSTRAINT customer_context_no_authority CHECK (authority_granted = FALSE),
  CONSTRAINT customer_context_no_execution CHECK (execution_effect = 'none')
);
CREATE INDEX IF NOT EXISTS idx_customer_context_workspace_state ON customer_relationship_context(workspace_id, lifecycle_state);
ALTER TABLE customer_relationship_context ENABLE ROW LEVEL SECURITY;
