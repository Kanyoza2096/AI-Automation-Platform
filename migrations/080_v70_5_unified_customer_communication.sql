-- V70.5 — Unified customer communication admission/audit state.
-- Transport credentials and provider execution remain outside this table.
CREATE TABLE IF NOT EXISTS customer_communication_admissions (
    communication_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    channel TEXT NOT NULL,
    disposition TEXT NOT NULL,
    governed_action TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, event_id, intent, channel)
);

CREATE INDEX IF NOT EXISTS idx_customer_communication_workspace_customer
    ON customer_communication_admissions (workspace_id, customer_id);

CREATE INDEX IF NOT EXISTS idx_customer_communication_correlation
    ON customer_communication_admissions (workspace_id, correlation_id);
