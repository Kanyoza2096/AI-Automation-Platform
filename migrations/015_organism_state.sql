-- Phase F — Durable organism state (survive process restart)

CREATE TABLE IF NOT EXISTS organism_state (
    workspace_id        TEXT PRIMARY KEY,
    plan                TEXT NOT NULL DEFAULT 'free',
    director_authority  TEXT,
    industry_organs     JSONB NOT NULL DEFAULT '["default"]'::jsonb,
    timezone            TEXT NOT NULL DEFAULT 'UTC',
    stats               JSONB NOT NULL DEFAULT '{}'::jsonb,
    vitals              JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_organism_state_plan ON organism_state(plan);

ALTER TABLE organism_state ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS organism_state_isolation ON organism_state;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

CREATE POLICY organism_state_isolation ON organism_state
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    );
