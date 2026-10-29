-- Phase 16.4: canonical PostgreSQL queue for governed KDO engineering builds.
-- Apply through the project's normal migration process. Do not grant browser roles.
CREATE TABLE IF NOT EXISTS public.kdo_engineering_build_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^[0-9a-fA-F]{64}$'),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK (status IN ('queued','running','awaiting_review','failed','uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kdo_build_jobs_scope_idempotency_uq UNIQUE (tenant_id, workspace_id, idempotency_key),
    CONSTRAINT kdo_build_jobs_scope_task_uq UNIQUE (tenant_id, workspace_id, task_id),
    CONSTRAINT kdo_build_jobs_lease_consistency CHECK (
      (status = 'running' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
      OR (status <> 'running' AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_ready
  ON public.kdo_engineering_build_jobs(status, created_at, job_id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_expired
  ON public.kdo_engineering_build_jobs(lease_expires_at, job_id)
  WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_scope
  ON public.kdo_engineering_build_jobs(tenant_id, workspace_id, status, updated_at);

ALTER TABLE public.kdo_engineering_build_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_build_jobs FROM PUBLIC, anon, authenticated;
-- Server-side service_role is the intended access path. The adapter must use
-- server-only credentials; never expose a service-role key to a browser client.
GRANT SELECT, INSERT, UPDATE ON TABLE public.kdo_engineering_build_jobs TO service_role;
COMMENT ON TABLE public.kdo_engineering_build_jobs IS
  'KDO Phase 16.4 shared durable engineering queue; execution only after persisted plan approval and Council/Action Kernel admission; no release authority.';
