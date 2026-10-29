-- Phase 16.9: durable, workspace-scoped engineering console events.
-- Apply with the normal migration runner after 141_kdo_engineering_shared_metadata.sql.
CREATE TABLE IF NOT EXISTS public.kdo_engineering_console_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    sequence_no BIGINT NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[0-9a-fA-F]{64}$'),
    UNIQUE (tenant_id, workspace_id, task_id, sequence_no),
    FOREIGN KEY (tenant_id, workspace_id, task_id)
      REFERENCES public.kdo_engineering_tasks(tenant_id, workspace_id, task_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_kdo_eng_console_events_task_sequence
    ON public.kdo_engineering_console_events(tenant_id, workspace_id, task_id, sequence_no);
ALTER TABLE public.kdo_engineering_console_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.kdo_engineering_console_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.kdo_engineering_console_events TO service_role;
COMMENT ON TABLE public.kdo_engineering_console_events IS
  'Persisted engineering console event history. Events are workspace-scoped and hash-chained; not a substitute for the platform execution ledger.';
