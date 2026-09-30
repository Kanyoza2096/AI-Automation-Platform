-- V71.4 Agent Planning Plane. Planning is not execution authority.
CREATE TABLE IF NOT EXISTS agent_plans (
    plan_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    version TEXT NOT NULL DEFAULT '1',
    plan_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agent_plans_status_ck CHECK (status IN ('draft','ready','blocked','cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_agent_plans_workspace_agent ON agent_plans(workspace_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_plans_status ON agent_plans(workspace_id, status);
