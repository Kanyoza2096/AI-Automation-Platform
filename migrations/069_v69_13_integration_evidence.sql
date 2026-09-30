-- V69.13 — tenant-scoped append-only integration evidence.
-- No credentials, raw provider payloads, or authority are stored here.
CREATE TABLE IF NOT EXISTS integration_evidence (
    evidence_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    connector_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    action TEXT NOT NULL DEFAULT '',
    correlation_id TEXT,
    actor TEXT NOT NULL DEFAULT 'system',
    provider TEXT NOT NULL DEFAULT '',
    connector_version TEXT NOT NULL DEFAULT '',
    payload_fingerprint CHAR(64) NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_hash CHAR(64) NOT NULL DEFAULT '',
    evidence_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT integration_evidence_workspace_connection_fk
        FOREIGN KEY (workspace_id, connection_id)
        REFERENCES integration_connections(workspace_id, connection_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_integration_evidence_workspace_time
    ON integration_evidence(workspace_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_evidence_connection_time
    ON integration_evidence(workspace_id, connection_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_evidence_correlation
    ON integration_evidence(workspace_id, correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE integration_evidence ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS integration_evidence_member_select ON integration_evidence;
CREATE POLICY integration_evidence_member_select
    ON integration_evidence FOR SELECT
    USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()));

-- Writes should occur through a trusted server-side evidence adapter.
DROP POLICY IF EXISTS integration_evidence_no_client_write ON integration_evidence;
CREATE POLICY integration_evidence_no_client_write
    ON integration_evidence FOR INSERT
    WITH CHECK (false);
