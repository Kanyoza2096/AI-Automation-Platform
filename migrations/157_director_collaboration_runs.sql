-- Persistent, workspace-isolated Director engine collaboration audit records.
-- All reads and writes are performed by the authenticated backend service role.
CREATE TABLE IF NOT EXISTS public.director_collaboration_runs (
    id UUID PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    conversation_id UUID,
    created_by TEXT NOT NULL DEFAULT 'authenticated',
    objective TEXT NOT NULL,
    requested_engines TEXT[] NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    consultations JSONB NOT NULL DEFAULT '[]'::JSONB,
    synthesis TEXT,
    synthesis_provider TEXT,
    synthesis_model TEXT,
    evidence_summary JSONB NOT NULL DEFAULT '{}'::JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT director_collaboration_objective_length
        CHECK (char_length(objective) BETWEEN 1 AND 6000),
    CONSTRAINT director_collaboration_engines_count
        CHECK (cardinality(requested_engines) BETWEEN 1 AND 4)
);

CREATE INDEX IF NOT EXISTS idx_director_collaboration_workspace_created
    ON public.director_collaboration_runs (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_director_collaboration_conversation
    ON public.director_collaboration_runs (workspace_id, conversation_id, created_at DESC);

ALTER TABLE public.director_collaboration_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_collaboration_runs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.director_collaboration_runs TO service_role;

COMMENT ON TABLE public.director_collaboration_runs IS
    'Workspace-scoped advisory engine collaboration records; browser roles have no direct access.';
