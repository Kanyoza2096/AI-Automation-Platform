-- V66.6 Learning feedback + effectiveness evaluation.
-- Evaluations are observational/advisory and cannot authorize actions.
CREATE TABLE IF NOT EXISTS business_learning_effectiveness (
  evaluation_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  knowledge_id text NOT NULL,
  evaluation_state text NOT NULL CHECK (evaluation_state IN ('effective','ineffective','mixed','insufficient_evidence','review_required')),
  sample_count integer NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
  success_count integer NOT NULL DEFAULT 0 CHECK (success_count >= 0),
  failure_count integer NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
  uncertain_count integer NOT NULL DEFAULT 0 CHECK (uncertain_count >= 0),
  success_rate numeric NOT NULL CHECK (success_rate >= 0 AND success_rate <= 1),
  baseline_success_rate numeric CHECK (baseline_success_rate IS NULL OR (baseline_success_rate >= 0 AND baseline_success_rate <= 1)),
  baseline_sample_count integer NOT NULL DEFAULT 0 CHECK (baseline_sample_count >= 0),
  lift numeric CHECK (lift IS NULL OR (lift >= -1 AND lift <= 1)),
  confidence numeric NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  causal_claim text NOT NULL DEFAULT 'association_only' CHECK (causal_claim = 'association_only'),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, knowledge_id, evaluation_id)
);

CREATE INDEX IF NOT EXISTS idx_learning_effectiveness_workspace
  ON business_learning_effectiveness(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_effectiveness_knowledge
  ON business_learning_effectiveness(workspace_id, knowledge_id, created_at DESC);

ALTER TABLE business_learning_effectiveness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_learning_effectiveness_workspace_isolation ON business_learning_effectiveness;
CREATE POLICY business_learning_effectiveness_workspace_isolation
  ON business_learning_effectiveness
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
