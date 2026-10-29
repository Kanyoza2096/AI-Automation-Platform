-- R13: conversational discovery state only.
-- This is orchestration state, NOT a Knowledge Plane replacement.
CREATE TABLE IF NOT EXISTS business_discovery_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','READY','HANDOFF')),
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_business_discovery_sessions_workspace
    ON business_discovery_sessions(workspace_id, updated_at DESC);
ALTER TABLE business_discovery_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY business_discovery_sessions_workspace_select
    ON business_discovery_sessions FOR SELECT
    USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
CREATE POLICY business_discovery_sessions_workspace_insert
    ON business_discovery_sessions FOR INSERT
    WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
CREATE POLICY business_discovery_sessions_workspace_update
    ON business_discovery_sessions FOR UPDATE
    USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text))
    WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
