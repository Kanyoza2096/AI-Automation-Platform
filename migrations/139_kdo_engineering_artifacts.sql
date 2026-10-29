-- KDO AI Engineering Vertical — Phase 3 artifact manifest/blob metadata.
-- Production rollout note: store large content in the approved object store and
-- keep a content-addressed manifest here; do not use this schema as authorization.
-- Tenant/workspace RLS must be integrated with KDO's canonical identity policies.
CREATE TABLE IF NOT EXISTS kdo_engineering_artifacts (
    artifact_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'repository_snapshot', 'patch', 'test_log', 'build_log', 'review_report'
    )),
    filename TEXT NOT NULL,
    relative_path TEXT,
    base_revision TEXT,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    storage_ref TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kdo_engineering_artifacts_scope_task
    ON kdo_engineering_artifacts (tenant_id, workspace_id, task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kdo_engineering_artifacts_digest
    ON kdo_engineering_artifacts (tenant_id, workspace_id, sha256);
