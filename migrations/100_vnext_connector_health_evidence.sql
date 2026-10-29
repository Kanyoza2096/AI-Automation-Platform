-- vNext Connector Fabric: durable health observation evidence.
-- Evidence is descriptive/eligibility input only; it never grants execution authority.
CREATE TABLE IF NOT EXISTS connector_health_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    connector_key TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    health_state TEXT NOT NULL CHECK (health_state IN ('healthy','degraded','unavailable','unknown')),
    drift JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider_status_code INTEGER,
    latency_ms INTEGER,
    evidence_ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_connector_health_workspace_connector_time
    ON connector_health_observations(workspace_id, connector_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_health_connection_time
    ON connector_health_observations(workspace_id, connection_id, observed_at DESC);

ALTER TABLE connector_health_observations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_health_observations_member_select ON connector_health_observations;
CREATE POLICY connector_health_observations_member_select
    ON connector_health_observations FOR SELECT USING (EXISTS (
        SELECT 1 FROM workspace_members wm
        WHERE wm.workspace_id = connector_health_observations.workspace_id
          AND wm.user_id = auth.uid()::text
    ));
