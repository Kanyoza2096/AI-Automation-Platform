-- V71.9 — Governed AI Workforce: Bounded Autonomous Business Operations
CREATE TABLE IF NOT EXISTS agent_autonomous_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    event_json TEXT NOT NULL DEFAULT '{}',
    budget_id TEXT,
    coordination_id TEXT,
    takeover_case_id TEXT,
    status TEXT NOT NULL,
    disposition TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1',
    result_summary_json TEXT NOT NULL DEFAULT '{}',
    run_fingerprint TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_workspace_status
ON agent_autonomous_runs(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_workspace_agent
ON agent_autonomous_runs(workspace_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_workspace_plan
ON agent_autonomous_runs(workspace_id, plan_id);
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_workspace_workflow
ON agent_autonomous_runs(workspace_id, workflow_id);
