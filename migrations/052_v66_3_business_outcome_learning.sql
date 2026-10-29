-- V66.3 Business Outcome Learning
-- Learning is descriptive evidence. It cannot authorize, approve, or mutate policy.
CREATE TABLE IF NOT EXISTS business_outcome_learning_signals (
  signal_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  execution_id text,
  observation_id text NOT NULL,
  attribution_id text,
  target_type text NOT NULL CHECK (target_type IN ('action','decision','workflow','customer_outcome')),
  target_id text NOT NULL,
  learning_state text NOT NULL CHECK (learning_state IN ('positive','negative','uncertain','insufficient_evidence')),
  confidence numeric NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  lesson text NOT NULL,
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, observation_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_business_outcome_learning_target
  ON business_outcome_learning_signals(workspace_id, target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_outcome_learning_state
  ON business_outcome_learning_signals(workspace_id, learning_state, created_at DESC);

ALTER TABLE business_outcome_learning_signals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_outcome_learning_workspace_isolation ON business_outcome_learning_signals;
CREATE POLICY business_outcome_learning_workspace_isolation
  ON business_outcome_learning_signals
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
