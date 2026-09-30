-- V71.5 Agent Evaluation Plane. Evaluation is measurement, never authority.
CREATE TABLE IF NOT EXISTS agent_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    version TEXT NOT NULL DEFAULT '1',
    evaluation_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agent_evaluations_status_ck CHECK (status IN ('evaluated','insufficient_evidence','invalid'))
);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_workspace_agent ON agent_evaluations(workspace_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_workspace_plan ON agent_evaluations(workspace_id, plan_id);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_status ON agent_evaluations(workspace_id, status);
