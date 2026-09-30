-- V66.1 Outcome Observation Ledger
-- Execution receipts are evidence of provider execution, not proof of business success.
CREATE TABLE IF NOT EXISTS business_outcome_observations (
  observation_id text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  execution_id text NOT NULL,
  idempotency_key text NOT NULL,
  action text,
  observation_type text NOT NULL CHECK (observation_type IN ('execution_receipt','business_outcome','external_signal')),
  execution_status text,
  outcome_state text NOT NULL,
  provider text,
  provider_object_id text,
  observed_at timestamptz NOT NULL,
  source text NOT NULL,
  source_trust text NOT NULL,
  business_success boolean,
  business_success_confidence numeric,
  attribution_status text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  authority text NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  fingerprint text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_business_outcome_observations_execution
  ON business_outcome_observations(workspace_id, execution_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_outcome_observations_type
  ON business_outcome_observations(workspace_id, observation_type, observed_at DESC);

ALTER TABLE business_outcome_observations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_outcome_observations_workspace_isolation ON business_outcome_observations;
CREATE POLICY business_outcome_observations_workspace_isolation
  ON business_outcome_observations
  USING (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('request.jwt.claim.workspace_id', true)::uuid);
