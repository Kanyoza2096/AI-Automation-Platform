-- Phase 16.5: canonical PostgreSQL task/workspace/approval metadata.
-- Apply after migration 140. Browser roles must never write engineering authority records.
CREATE TABLE IF NOT EXISTS public.kdo_engineering_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria JSONB NOT NULL CHECK (jsonb_typeof(acceptance_criteria) = 'object'),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed','planned','queued','running','awaiting_review','awaiting_approval','completed','failed','blocked','cancelled')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    repository_ref TEXT,
    base_revision TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-fA-F]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kdo_eng_tasks_scope_idempotency_uq UNIQUE (tenant_id,workspace_id,idempotency_key),
    CONSTRAINT kdo_eng_tasks_scope_identity_uq UNIQUE (tenant_id,workspace_id,task_id)
);
CREATE INDEX IF NOT EXISTS idx_kdo_eng_tasks_scope_updated
  ON public.kdo_engineering_tasks(tenant_id,workspace_id,updated_at DESC,task_id);
CREATE INDEX IF NOT EXISTS idx_kdo_eng_tasks_scope_status
  ON public.kdo_engineering_tasks(tenant_id,workspace_id,status,updated_at);

CREATE TABLE IF NOT EXISTS public.kdo_engineering_workspaces (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL,
    base_revision TEXT NOT NULL,
    head_revision TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('provisioning','active','awaiting_review','interrupted_or_failed','interrupted_worker_confirmed_absent','workspace_missing_or_uninspectable','drift_detected','reconciled','paused','completed','failed','cleanup_pending','removed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,workspace_id,task_id),
    CONSTRAINT kdo_eng_workspaces_task_fk FOREIGN KEY (tenant_id,workspace_id,task_id)
      REFERENCES public.kdo_engineering_tasks(tenant_id,workspace_id,task_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_kdo_eng_workspaces_scope
  ON public.kdo_engineering_workspaces(tenant_id,workspace_id,status,updated_at);

CREATE TABLE IF NOT EXISTS public.kdo_engineering_plan_reviews (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^[0-9a-fA-F]{64}$'),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,workspace_id,task_id),
    CONSTRAINT kdo_eng_plan_reviews_task_fk FOREIGN KEY (tenant_id,workspace_id,task_id)
      REFERENCES public.kdo_engineering_tasks(tenant_id,workspace_id,task_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_kdo_eng_plan_reviews_scope
  ON public.kdo_engineering_plan_reviews(tenant_id,workspace_id,created_at DESC);

ALTER TABLE public.kdo_engineering_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kdo_engineering_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kdo_engineering_plan_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_tasks, public.kdo_engineering_workspaces,
  public.kdo_engineering_plan_reviews FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.kdo_engineering_tasks TO service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.kdo_engineering_workspaces TO service_role;
GRANT SELECT, INSERT ON TABLE public.kdo_engineering_plan_reviews TO service_role;
COMMENT ON TABLE public.kdo_engineering_tasks IS 'KDO Phase 16.5 canonical scoped task lifecycle and revision-CAS metadata; not an execution-authority token.';
COMMENT ON TABLE public.kdo_engineering_workspaces IS 'KDO Phase 16.5 shared workspace metadata; filesystem isolation must be enforced independently.';
COMMENT ON TABLE public.kdo_engineering_plan_reviews IS 'KDO Phase 16.5 append-once plan approval ledger; approval does not authorize merge, deployment, or release.';
