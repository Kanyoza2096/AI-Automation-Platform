-- Durable, append-only platform engineering change ledger.
-- Source connectors remain read-only; this table stores observed evidence only.
CREATE TABLE IF NOT EXISTS public.director_engineering_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('pull_request_merged', 'database_migration_merged', 'deployment', 'workflow_run')
    ),
    repository TEXT,
    source_id TEXT NOT NULL,
    event_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    commit_sha TEXT,
    evidence_source TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS director_engineering_events_event_at_idx
    ON public.director_engineering_events (event_at DESC NULLS LAST, created_at DESC);
CREATE INDEX IF NOT EXISTS director_engineering_events_repository_idx
    ON public.director_engineering_events (repository, event_at DESC NULLS LAST);

ALTER TABLE public.director_engineering_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_engineering_events FROM anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.director_engineering_events TO service_role;

COMMENT ON TABLE public.director_engineering_events IS
    'Append-only platform engineering evidence ledger. Source events are idempotent and never imply runtime correctness.';
COMMENT ON COLUMN public.director_engineering_events.event_key IS
    'Deterministic source identity; duplicate observations are ignored to preserve the original ledger event.';
COMMENT ON COLUMN public.director_engineering_events.event_type IS
    'A merged migration file is source evidence only; it does not prove the migration was applied.';
