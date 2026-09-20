-- V70.8 Customer Lifecycle & Retention Operating System
-- State/read-model storage only. Consequential execution remains in the canonical runtime.
CREATE TABLE IF NOT EXISTS customer_lifecycle_operating_state (
    workspace_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    health_score NUMERIC(6,2) NOT NULL DEFAULT 50.00,
    positive_signals NUMERIC(12,2) NOT NULL DEFAULT 0,
    negative_signals NUMERIC(12,2) NOT NULL DEFAULT 0,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_event_id TEXT NOT NULL,
    last_event_name TEXT NOT NULL,
    recommended_workflow TEXT,
    recommendation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, customer_id),
    CHECK (health_score >= 0 AND health_score <= 100),
    CHECK (interaction_count >= 0),
    CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_customer_lifecycle_workspace_stage
    ON customer_lifecycle_operating_state(workspace_id, stage);
CREATE INDEX IF NOT EXISTS idx_customer_lifecycle_workspace_health
    ON customer_lifecycle_operating_state(workspace_id, health_score);
CREATE INDEX IF NOT EXISTS idx_customer_lifecycle_last_event
    ON customer_lifecycle_operating_state(workspace_id, last_event_id);
