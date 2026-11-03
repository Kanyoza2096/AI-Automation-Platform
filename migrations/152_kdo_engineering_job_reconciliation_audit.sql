-- Phase 17.5: append-only audit trail for uncertain engineering jobs.
CREATE TABLE IF NOT EXISTS public.kdo_engineering_job_reconciliation_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES public.kdo_engineering_build_jobs(job_id),
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
      event_type IN ('worker_death_confirmed','replay_authorized')
    ),
    from_status TEXT NOT NULL CHECK (from_status IN ('running','uncertain')),
    to_status TEXT NOT NULL CHECK (to_status IN ('uncertain','queued')),
    actor_id TEXT NOT NULL,
    approval_ref TEXT,
    evidence_digest TEXT CHECK (
      evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-fA-F]{64}$'
    ),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kdo_engineering_reconciliation_event_transition_ck CHECK (
      (event_type='worker_death_confirmed' AND from_status='running' AND to_status='uncertain'
        AND actor_id='system:reconciler' AND approval_ref IS NULL AND evidence_digest IS NULL)
      OR
      (event_type='replay_authorized' AND from_status='uncertain' AND to_status='queued'
        AND actor_id <> 'system:reconciler' AND approval_ref IS NOT NULL AND evidence_digest IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_kdo_engineering_reconciliation_scope
  ON public.kdo_engineering_job_reconciliation_events
  (tenant_id, workspace_id, job_id, created_at);

ALTER TABLE public.kdo_engineering_job_reconciliation_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_job_reconciliation_events
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.kdo_engineering_job_reconciliation_events
  TO service_role;
REVOKE UPDATE, DELETE ON TABLE public.kdo_engineering_job_reconciliation_events
  FROM service_role;

-- The service_role has SELECT/INSERT only. UPDATE/DELETE are revoked so
-- application-level audit history is append-only; database owners remain trusted.

COMMENT ON TABLE public.kdo_engineering_job_reconciliation_events IS
  'Append-only audit history for confirmed worker death and human-authorized uncertain job replay with no automatic replay.';
