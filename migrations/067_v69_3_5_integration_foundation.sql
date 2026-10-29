-- V69.3-V69.5 Integration foundation. Secret material is never stored here.
CREATE TABLE IF NOT EXISTS integration_credential_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    credential_kind TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    external_subject TEXT DEFAULT '',
    fingerprint TEXT DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(workspace_id, connection_id)
);

CREATE TABLE IF NOT EXISTS integration_capability_discoveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    connector_key TEXT NOT NULL,
    declared_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    discovered_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    declared_events JSONB NOT NULL DEFAULT '[]'::jsonb,
    declared_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    tenant_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS integration_event_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    external_event TEXT NOT NULL,
    canonical_event TEXT NOT NULL,
    mapping JSONB NOT NULL DEFAULT '[]'::jsonb,
    tenant_field TEXT,
    stable_event_id_field TEXT,
    version TEXT NOT NULL DEFAULT '1',
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(workspace_id, connection_id, external_event, version)
);

CREATE INDEX IF NOT EXISTS idx_integration_credentials_workspace ON integration_credential_bindings(workspace_id);
CREATE INDEX IF NOT EXISTS idx_integration_discovery_workspace ON integration_capability_discoveries(workspace_id);
CREATE INDEX IF NOT EXISTS idx_integration_mappings_workspace ON integration_event_mappings(workspace_id);

ALTER TABLE integration_credential_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_capability_discoveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_event_mappings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS integration_credential_member_select ON integration_credential_bindings;
CREATE POLICY integration_credential_member_select ON integration_credential_bindings FOR SELECT USING (EXISTS (
    SELECT 1 FROM workspace_members wm WHERE wm.workspace_id = integration_credential_bindings.workspace_id AND wm.user_id = auth.uid()::text
));
DROP POLICY IF EXISTS integration_discovery_member_select ON integration_capability_discoveries;
CREATE POLICY integration_discovery_member_select ON integration_capability_discoveries FOR SELECT USING (EXISTS (
    SELECT 1 FROM workspace_members wm WHERE wm.workspace_id = integration_capability_discoveries.workspace_id AND wm.user_id = auth.uid()::text
));
DROP POLICY IF EXISTS integration_mapping_member_select ON integration_event_mappings;
CREATE POLICY integration_mapping_member_select ON integration_event_mappings FOR SELECT USING (EXISTS (
    SELECT 1 FROM workspace_members wm WHERE wm.workspace_id = integration_event_mappings.workspace_id AND wm.user_id = auth.uid()::text
));
