-- V70.11 — Cross-domain orchestration state.
-- Durable state only; execution remains owned by existing governed runtime.
CREATE TABLE IF NOT EXISTS business_cross_domain_orchestrations (
    workspace_id TEXT NOT NULL,
    orchestration_id TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0,
    completed_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    failed_workflow TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, orchestration_id),
    UNIQUE (workspace_id, source_event_id)
);
CREATE INDEX IF NOT EXISTS idx_cross_domain_orch_workspace_status
    ON business_cross_domain_orchestrations(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_cross_domain_orch_objective
    ON business_cross_domain_orchestrations(workspace_id, objective_id);
