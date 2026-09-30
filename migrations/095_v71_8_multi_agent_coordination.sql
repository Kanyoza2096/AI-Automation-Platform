-- V71.8 — Governed AI Workforce: Multi-Agent Coordination
CREATE TABLE IF NOT EXISTS agent_coordination_requests (
    coordination_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    coordinator_agent_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    tasks_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1',
    coordination_fingerprint TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (coordinator_agent_id <> target_agent_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_coordination_workspace_status
ON agent_coordination_requests(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_coordination_workspace_target
ON agent_coordination_requests(workspace_id, target_agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_coordination_workspace_coordinator
ON agent_coordination_requests(workspace_id, coordinator_agent_id);
