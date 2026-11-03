-- Phase 16.5: shared PostgreSQL task, workspace and plan-review persistence.
-- Server-side service_role only. Apply through the normal migration pipeline.
CREATE TABLE IF NOT EXISTS public.kdo_engineering_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    repository_ref TEXT,
    base_revision TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kdo_engineering_tasks_scope_idempotency_uq
      UNIQUE (tenant_id, workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_kdo_engineering_tasks_scope_updated
  ON public.kdo_engineering_tasks(tenant_id, workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.kdo_engineering_workspaces (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES public.kdo_engineering_tasks(task_id),
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL,
    base_revision TEXT NOT NULL,
    head_revision TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_kdo_engineering_workspaces_scope
  ON public.kdo_engineering_workspaces(tenant_id, workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS public.kdo_engineering_plan_reviews (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^[0-9a-fA-F]{64}$'),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, task_id),
    FOREIGN KEY (tenant_id, workspace_id, task_id)
      REFERENCES public.kdo_engineering_workspaces(tenant_id, workspace_id, task_id)
);

ALTER TABLE public.kdo_engineering_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kdo_engineering_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kdo_engineering_plan_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_tasks,
  public.kdo_engineering_workspaces, public.kdo_engineering_plan_reviews
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.kdo_engineering_tasks,
  public.kdo_engineering_workspaces, public.kdo_engineering_plan_reviews
  TO service_role;
COMMENT ON TABLE public.kdo_engineering_tasks IS
  'KDO Phase 16.5 shared task contracts; scope-keyed and revision-fenced; no execution or release authority.';
COMMENT ON TABLE public.kdo_engineering_workspaces IS
  'KDO Phase 16.5 durable metadata for isolated task worktrees; paths are metadata, not authorization.';
COMMENT ON TABLE public.kdo_engineering_plan_reviews IS
  'KDO Phase 16.5 immutable first plan decision per task scope; does not authorize release.';
