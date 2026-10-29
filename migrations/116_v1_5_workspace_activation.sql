-- V1.5 Workspace Activation & First Work.
-- Durable activation state; no secrets and no provider authority.
CREATE TABLE IF NOT EXISTS workspace_activations (
  activation_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  onboarding_plan_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'checking' CHECK (status IN ('checking','blocked','ready','activating','active','failed')),
  readiness JSONB NOT NULL DEFAULT '{}'::jsonb,
  activated_workers JSONB NOT NULL DEFAULT '[]'::jsonb,
  first_task_id TEXT,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, onboarding_plan_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_activations_workspace ON workspace_activations(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS workspace_activation_tasks (
  task_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  activation_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed')),
  risk_level TEXT NOT NULL DEFAULT 'low',
  policy_decision TEXT NOT NULL DEFAULT 'allow',
  input JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error TEXT,
  UNIQUE(workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_workspace_activation_tasks_workspace ON workspace_activation_tasks(workspace_id, created_at DESC);

ALTER TABLE workspace_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_activation_tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_activation_member_select ON workspace_activations;
CREATE POLICY workspace_activation_member_select ON workspace_activations FOR SELECT USING (public.is_workspace_member(workspace_id));
DROP POLICY IF EXISTS workspace_activation_task_member_select ON workspace_activation_tasks;
CREATE POLICY workspace_activation_task_member_select ON workspace_activation_tasks FOR SELECT USING (public.is_workspace_member(workspace_id));
