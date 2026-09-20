-- V70.10 Business Command & Planning
CREATE TABLE IF NOT EXISTS business_plans (
    workspace_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    status TEXT NOT NULL,
    capacity_budget DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacity_used DOUBLE PRECISION NOT NULL DEFAULT 0,
    unmet_capacity DOUBLE PRECISION NOT NULL DEFAULT 0,
    tradeoffs JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, plan_id),
    CHECK (status IN ('draft','ready','approved','executing','completed','cancelled')),
    CHECK (capacity_budget >= 0 AND capacity_used >= 0 AND unmet_capacity >= 0),
    CHECK (version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_business_plans_workspace_objective ON business_plans(workspace_id, objective_id);
CREATE INDEX IF NOT EXISTS idx_business_plans_workspace_status ON business_plans(workspace_id, status);

CREATE TABLE IF NOT EXISTS business_plan_commands (
    workspace_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, command_id),
    UNIQUE (workspace_id, plan_id, item_id),
    CHECK (status IN ('accepted','dispatched','completed','failed','cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_business_plan_commands_plan ON business_plan_commands(workspace_id, plan_id);
