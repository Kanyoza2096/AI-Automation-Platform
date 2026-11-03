-- Phase 16.6: approval ledger must exist before the execution workspace.
-- Plan approval occurs before a worktree is created, so bind the immutable
-- review row to the task's tenant/workspace scope rather than the workspace row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kdo_engineering_tasks_scope_task_id_uq
  ON public.kdo_engineering_tasks(tenant_id, workspace_id, task_id);

ALTER TABLE public.kdo_engineering_plan_reviews
  DROP CONSTRAINT IF EXISTS kdo_engineering_plan_reviews_tenant_id_workspace_id_task_id_fkey;

ALTER TABLE public.kdo_engineering_plan_reviews
  ADD CONSTRAINT kdo_engineering_plan_reviews_task_scope_fkey
  FOREIGN KEY (tenant_id, workspace_id, task_id)
  REFERENCES public.kdo_engineering_tasks(tenant_id, workspace_id, task_id);

ALTER TABLE public.kdo_engineering_plan_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_plan_reviews FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.kdo_engineering_plan_reviews TO service_role;
