-- Durable storage required by Director strategic-plan persistence.
-- Strategic plans are platform-governed records; browser roles receive no direct access.
CREATE TABLE IF NOT EXISTS public.director_strategic_plans (
    plan_id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    authority TEXT NOT NULL DEFAULT 'none',
    advisory_only BOOLEAN NOT NULL DEFAULT TRUE,
    execution_started BOOLEAN NOT NULL DEFAULT FALSE,
    tasks_created BOOLEAN NOT NULL DEFAULT FALSE,
    source_context_fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT director_strategic_plans_authority_none
        CHECK (authority = 'none'),
    CONSTRAINT director_strategic_plans_advisory_only
        CHECK (advisory_only IS TRUE),
    CONSTRAINT director_strategic_plans_no_execution
        CHECK (execution_started IS FALSE AND tasks_created IS FALSE),
    CONSTRAINT director_strategic_plans_payload_object
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_director_strategic_plans_created_at
    ON public.director_strategic_plans (created_at DESC);

ALTER TABLE public.director_strategic_plans ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_strategic_plans FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.director_strategic_plans TO service_role;

COMMENT ON TABLE public.director_strategic_plans IS
    'Platform-governed advisory Director plans; no execution authority is persisted here.';
