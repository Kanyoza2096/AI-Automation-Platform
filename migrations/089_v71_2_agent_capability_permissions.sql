-- V71.2 — Agent capability permissions. Permission is not business authority.
CREATE TABLE IF NOT EXISTS agent_capability_permissions (
    permission_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('allow','deny')),
    scope_json TEXT NOT NULL DEFAULT '{}',
    version TEXT NOT NULL DEFAULT '1',
    permission_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, agent_id, capability, permission_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_capability_permissions_agent
    ON agent_capability_permissions(workspace_id, agent_id, capability);
