-- V66.2 Outcome Attribution + Measurement
-- Attribution is descriptive evidence only. It never grants execution authority.
CREATE TABLE IF NOT EXISTS business_outcome_attributions (
  attribution_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  observation_id text NOT NULL,
  execution_id text,
  decision_id text,
  evaluation_id text,
  candidate_id text,
  handoff_id text,
  outcome_state text,
  business_success boolean,
  confidence numeric,
  attribution_status text NOT NULL CHECK (attribution_status IN ('attributed','unattributed','review_required')),
  attribution_method text NOT NULL CHECK (attribution_method IN ('exact_execution_id','external_event_id','none','ambiguous')),
  attribution_window jsonb NOT NULL DEFAULT '{}'::jsonb,
  external_event_id text,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  fingerprint text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_business_outcome_attributions_execution
  ON business_outcome_attributions(workspace_id, execution_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_outcome_attributions_status
  ON business_outcome_attributions(workspace_id, attribution_status, created_at DESC);

ALTER TABLE business_outcome_attributions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_outcome_attributions_workspace_isolation ON business_outcome_attributions;
CREATE POLICY business_outcome_attributions_workspace_isolation
  ON business_outcome_attributions
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
