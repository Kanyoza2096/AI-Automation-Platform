-- V66.5 Learning -> Decision-Input integration.
-- This table stores advisory projections only; it cannot authorize execution.
CREATE TABLE IF NOT EXISTS business_learning_decision_context (
  context_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  understanding_id text NOT NULL,
  knowledge jsonb NOT NULL DEFAULT '[]'::jsonb,
  knowledge_count integer NOT NULL DEFAULT 0 CHECK (knowledge_count >= 0 AND knowledge_count <= 10),
  advisory_only boolean NOT NULL DEFAULT true CHECK (advisory_only = true),
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  policy_effect text NOT NULL DEFAULT 'none' CHECK (policy_effect = 'none'),
  approval_effect text NOT NULL DEFAULT 'none' CHECK (approval_effect = 'none'),
  execution_effect text NOT NULL DEFAULT 'none' CHECK (execution_effect = 'none'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, understanding_id)
);

CREATE INDEX IF NOT EXISTS idx_learning_decision_context_workspace
  ON business_learning_decision_context(workspace_id, created_at DESC);

ALTER TABLE business_learning_decision_context ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_learning_decision_context_workspace_isolation ON business_learning_decision_context;
CREATE POLICY business_learning_decision_context_workspace_isolation
  ON business_learning_decision_context
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
