-- V71.6 Agent Cost & Risk Budgets. Measurement/admission boundary only.
CREATE TABLE IF NOT EXISTS agent_budgets (
    budget_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    max_actions BIGINT,
    max_cost NUMERIC,
    max_risk_units NUMERIC,
    status TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    used_actions BIGINT NOT NULL DEFAULT 0,
    used_cost NUMERIC NOT NULL DEFAULT 0,
    used_risk_units NUMERIC NOT NULL DEFAULT 0,
    budget_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agent_budgets_status_ck CHECK (status IN ('active','exhausted','suspended','expired')),
    CONSTRAINT agent_budgets_nonnegative_ck CHECK (used_actions >= 0 AND used_cost >= 0 AND used_risk_units >= 0),
    CONSTRAINT agent_budgets_limit_ck CHECK ((max_actions IS NULL OR max_actions >= 0) AND (max_cost IS NULL OR max_cost >= 0) AND (max_risk_units IS NULL OR max_risk_units >= 0)),
    CONSTRAINT agent_budgets_has_limit_ck CHECK (max_actions IS NOT NULL OR max_cost IS NOT NULL OR max_risk_units IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_agent_budgets_workspace_agent ON agent_budgets(workspace_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_budgets_status ON agent_budgets(workspace_id, status);
