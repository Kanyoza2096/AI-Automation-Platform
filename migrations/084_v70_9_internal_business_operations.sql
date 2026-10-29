-- V70.9 Internal Business Operations Operating System
CREATE TABLE IF NOT EXISTS internal_business_work_state (
    workspace_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    owner_id TEXT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'operations',
    created_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    last_event_id TEXT NOT NULL,
    last_event_name TEXT NOT NULL,
    sla_breached BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, work_id),
    CHECK (priority IN ('low','medium','high','critical')),
    CHECK (status IN ('open','assigned','in_progress','blocked','completed','failed','cancelled')),
    CHECK (version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_internal_work_workspace_status ON internal_business_work_state(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_internal_work_workspace_owner ON internal_business_work_state(workspace_id, owner_id);
CREATE INDEX IF NOT EXISTS idx_internal_work_workspace_due ON internal_business_work_state(workspace_id, due_at);
CREATE INDEX IF NOT EXISTS idx_internal_work_workspace_sla ON internal_business_work_state(workspace_id, sla_breached);
