CREATE TABLE IF NOT EXISTS integration_optimization_observations (
 workspace_id TEXT NOT NULL, observation_id UUID PRIMARY KEY, connector_key TEXT NOT NULL,
 action TEXT NOT NULL, samples INTEGER NOT NULL, success_rate DOUBLE PRECISION NOT NULL,
 ambiguous_rate DOUBLE PRECISION NOT NULL DEFAULT 0, p95_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
 cost_per_action DOUBLE PRECISION NOT NULL DEFAULT 0, outcome_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS integration_optimization_recommendations (
 workspace_id TEXT NOT NULL, recommendation_id UUID PRIMARY KEY, action TEXT NOT NULL,
 connector_key TEXT NOT NULL, score DOUBLE PRECISION NOT NULL, confidence DOUBLE PRECISION NOT NULL,
 reasons JSONB NOT NULL DEFAULT '[]'::jsonb, advisory_only BOOLEAN NOT NULL DEFAULT TRUE,
 authority_granted BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE integration_optimization_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_optimization_recommendations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS integration_optimization_observations_member_select ON integration_optimization_observations;
CREATE POLICY integration_optimization_observations_member_select ON integration_optimization_observations FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
DROP POLICY IF EXISTS integration_optimization_recommendations_member_select ON integration_optimization_recommendations;
CREATE POLICY integration_optimization_recommendations_member_select ON integration_optimization_recommendations FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
CREATE INDEX IF NOT EXISTS idx_integration_optimization_obs_action ON integration_optimization_observations(workspace_id,action,connector_key);
CREATE INDEX IF NOT EXISTS idx_integration_optimization_rec_action ON integration_optimization_recommendations(workspace_id,action,created_at DESC);
