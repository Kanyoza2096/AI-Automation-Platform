-- V71.7 — Governed AI Workforce: Human Takeover
CREATE TABLE IF NOT EXISTS agent_human_takeover_cases (
    case_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    trigger TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    claimed_by TEXT,
    disposition TEXT,
    human_note TEXT,
    version TEXT NOT NULL DEFAULT '1',
    case_fingerprint TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_handoff_workspace_status
ON agent_human_takeover_cases(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_handoff_workspace_agent
ON agent_human_takeover_cases(workspace_id, agent_id);
