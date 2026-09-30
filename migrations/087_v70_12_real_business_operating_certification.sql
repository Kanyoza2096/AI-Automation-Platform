-- V70.12 — durable certification evidence.
-- Evidence is tenant-scoped; live-provider and restart certification remain explicit controls.
CREATE TABLE IF NOT EXISTS business_operating_certification_runs (
    workspace_id TEXT NOT NULL,
    certification_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    passed_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    not_proven_count INTEGER NOT NULL,
    evidence_fingerprint TEXT NOT NULL,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, certification_id)
);
CREATE INDEX IF NOT EXISTS idx_business_operating_certification_workspace_status
    ON business_operating_certification_runs(workspace_id, status);
