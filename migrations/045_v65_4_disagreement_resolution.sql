-- V65.4: preserve AI/deterministic interpretation disagreements as auditable data.
-- No action authority is introduced by this migration.
CREATE TABLE IF NOT EXISTS business_understanding_resolutions (
    resolution_id TEXT PRIMARY KEY,
    understanding_id TEXT NOT NULL,
    workspace_id TEXT,
    message_id TEXT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    deterministic_intent TEXT,
    deterministic_confidence NUMERIC(4,3),
    ai_intent TEXT,
    ai_confidence NUMERIC(4,3),
    confidence_gap NUMERIC(4,3),
    ai_verified_evidence_count INTEGER NOT NULL DEFAULT 0,
    selected_intent TEXT NOT NULL,
    selected_source TEXT NOT NULL,
    reason TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bur_workspace_created
    ON business_understanding_resolutions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bur_message
    ON business_understanding_resolutions(message_id);
