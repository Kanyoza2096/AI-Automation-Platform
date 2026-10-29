-- V69.15: tenant-scoped integration policy/approval configuration.
-- No credentials and no execution authority are stored here.
CREATE TABLE IF NOT EXISTS integration_policies (
    workspace_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    allowed_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    denied_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    council_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    human_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    forbidden_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    allow_autonomous_low_risk BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE integration_policies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS integration_policies_member_select ON integration_policies;
CREATE POLICY integration_policies_member_select ON integration_policies
    FOR SELECT USING (workspace_id = current_setting('app.workspace_id', true));
-- Writes must be performed by the governed policy-management service; no client
-- policy write is exposed by this migration.
CREATE INDEX IF NOT EXISTS idx_integration_policies_workspace ON integration_policies(workspace_id);
