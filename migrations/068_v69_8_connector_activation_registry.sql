-- V69.8 connector activation/certification registry. Evidence only; no secrets or authority.
CREATE TABLE IF NOT EXISTS connector_certification_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    connector_key TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    registry_state TEXT NOT NULL,
    certification_version TEXT,
    certification_status TEXT,
    certification_fingerprint TEXT,
    live_provider_verified BOOLEAN NOT NULL DEFAULT FALSE,
    certified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    evidence_ref TEXT DEFAULT '',
    generation INTEGER NOT NULL DEFAULT 1,
    reason TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(workspace_id, connection_id, connector_key)
);

CREATE INDEX IF NOT EXISTS idx_connector_cert_registry_workspace ON connector_certification_registry(workspace_id);
CREATE INDEX IF NOT EXISTS idx_connector_cert_registry_state ON connector_certification_registry(workspace_id, registry_state);

ALTER TABLE connector_certification_registry ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_cert_registry_member_select ON connector_certification_registry;
CREATE POLICY connector_cert_registry_member_select ON connector_certification_registry FOR SELECT USING (EXISTS (
    SELECT 1 FROM workspace_members wm
    WHERE wm.workspace_id = connector_certification_registry.workspace_id
      AND wm.user_id = auth.uid()::text
));
