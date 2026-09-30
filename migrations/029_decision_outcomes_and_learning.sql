-- Phase 5: durable verified outcomes for the learning loop.
-- Append-only evidence; the decisions row remains the current aggregate.
CREATE TABLE IF NOT EXISTS decision_outcomes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  decision_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  correlation_id TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('success','failure','partial')),
  score NUMERIC CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
  source TEXT NOT NULL DEFAULT 'system',
  platform_post_id TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decision_outcomes_workspace_created
  ON decision_outcomes(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_decision
  ON decision_outcomes(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_correlation
  ON decision_outcomes(correlation_id) WHERE correlation_id IS NOT NULL;

ALTER TABLE decision_outcomes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS decision_outcomes_workspace_select ON decision_outcomes;
CREATE POLICY decision_outcomes_workspace_select ON decision_outcomes
  FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

DROP POLICY IF EXISTS decision_outcomes_workspace_insert ON decision_outcomes;
CREATE POLICY decision_outcomes_workspace_insert ON decision_outcomes
  FOR INSERT WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
