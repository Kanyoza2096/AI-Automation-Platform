-- V69.1/V69.2 Business Integration & Operating Onboarding control-plane data.
-- No credentials/secrets are stored here. Provider tokens belong in the
-- existing secret-management/authentication boundary.

CREATE TABLE IF NOT EXISTS business_operating_identities (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    industry TEXT DEFAULT '',
    description TEXT DEFAULT '',
    objectives JSONB NOT NULL DEFAULT '[]'::jsonb,
    operating_hours JSONB NOT NULL DEFAULT '{}'::jsonb,
    policies JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS integration_connections (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connector_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'discovered',
    granted_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    autonomy_mode TEXT NOT NULL DEFAULT 'observe',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_business_operating_identities_workspace
    ON business_operating_identities(workspace_id);
CREATE INDEX IF NOT EXISTS idx_integration_connections_workspace
    ON integration_connections(workspace_id);

ALTER TABLE business_operating_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_connections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS business_operating_identity_member_select ON business_operating_identities;
CREATE POLICY business_operating_identity_member_select ON business_operating_identities
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM workspace_members wm
        WHERE wm.workspace_id = business_operating_identities.workspace_id
          AND wm.user_id = auth.uid()::text
    ));

DROP POLICY IF EXISTS integration_connection_member_select ON integration_connections;
CREATE POLICY integration_connection_member_select ON integration_connections
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM workspace_members wm
        WHERE wm.workspace_id = integration_connections.workspace_id
          AND wm.user_id = auth.uid()::text
    ));
