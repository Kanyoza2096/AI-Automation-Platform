-- V66.4 Learning Governance + Promotion
-- Promoted learning is reusable knowledge, never authority.
CREATE TABLE IF NOT EXISTS business_learning_governance (
  knowledge_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  target_type text NOT NULL CHECK (target_type IN ('action','decision','workflow','customer_outcome')),
  target_id text NOT NULL,
  promotion_state text NOT NULL CHECK (promotion_state IN ('candidate','promoted','rejected','stale','contradicted','review_required')),
  direction text NOT NULL CHECK (direction IN ('positive','negative','mixed')),
  confidence numeric NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  support_count integer NOT NULL CHECK (support_count >= 0),
  consistency numeric NOT NULL CHECK (consistency >= 0 AND consistency <= 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  lesson text NOT NULL,
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_learning_governance_state
  ON business_learning_governance(workspace_id, promotion_state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_governance_target
  ON business_learning_governance(workspace_id, target_type, target_id);

ALTER TABLE business_learning_governance ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_learning_governance_workspace_isolation ON business_learning_governance;
CREATE POLICY business_learning_governance_workspace_isolation
  ON business_learning_governance
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
