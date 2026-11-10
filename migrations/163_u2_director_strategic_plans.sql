-- U2: durable, advisory Director strategic plan proposals.
-- This stores validated proposals only; it does not grant execution authority.
CREATE TABLE IF NOT EXISTS public.director_strategic_plans (
    plan_id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    authority TEXT NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
    advisory_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (advisory_only),
    execution_started BOOLEAN NOT NULL DEFAULT FALSE CHECK (NOT execution_started),
    tasks_created BOOLEAN NOT NULL DEFAULT FALSE CHECK (NOT tasks_created),
    source_context_fingerprint TEXT NOT NULL
        CHECK (length(source_context_fingerprint) = 64),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS director_strategic_plans_created_idx
    ON public.director_strategic_plans (created_at DESC);

ALTER TABLE public.director_strategic_plans ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.director_strategic_plans
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.director_strategic_plans
    TO service_role;

COMMENT ON TABLE public.director_strategic_plans IS
    'Durable, validated Director objective plans. Proposals only; no execution or approval authority is persisted here.';
