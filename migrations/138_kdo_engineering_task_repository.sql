-- KDO AI Engineering Vertical: durable task records.
-- Apply through the normal migration process; this file does not configure RLS
-- or replace the application's existing tenant/workspace authorization layer.
CREATE TABLE IF NOT EXISTS kdo_engineering_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'proposed','planned','queued','running','awaiting_review',
        'awaiting_approval','completed','failed','blocked','cancelled'
    )),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    repository_ref TEXT,
    base_revision TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_kdo_engineering_tasks_scope_updated
    ON kdo_engineering_tasks (tenant_id, workspace_id, updated_at DESC);
