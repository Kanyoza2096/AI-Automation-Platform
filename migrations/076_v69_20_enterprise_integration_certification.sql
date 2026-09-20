CREATE TABLE IF NOT EXISTS enterprise_integration_certification_runs (
 workspace_id TEXT NOT NULL,
 certification_id UUID PRIMARY KEY,
 certification_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pass','fail','not_proven')),
 evidence_fingerprint TEXT NOT NULL,
 findings JSONB NOT NULL DEFAULT '[]'::jsonb,
 authority_granted BOOLEAN NOT NULL DEFAULT FALSE,
 execution_effect TEXT NOT NULL DEFAULT 'none',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (authority_granted = FALSE),
 CHECK (execution_effect = 'none')
);
ALTER TABLE enterprise_integration_certification_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS enterprise_integration_certification_runs_member_select ON enterprise_integration_certification_runs;
CREATE POLICY enterprise_integration_certification_runs_member_select
 ON enterprise_integration_certification_runs FOR SELECT
 USING (workspace_id=current_setting('app.workspace_id',true));
CREATE INDEX IF NOT EXISTS idx_enterprise_integration_cert_runs_workspace
 ON enterprise_integration_certification_runs(workspace_id, created_at DESC);
