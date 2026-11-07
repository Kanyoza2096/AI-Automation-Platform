-- U2: canonical, workspace-scoped OrganismRuntime snapshots.
-- Observations remain recoverable from the existing durable events ledger.
CREATE TABLE IF NOT EXISTS public.organism_runtime_snapshots (
    workspace_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    state JSONB NOT NULL,
    state_sha256 TEXT NOT NULL CHECK (length(state_sha256) = 64),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS organism_runtime_snapshots_updated_idx
    ON public.organism_runtime_snapshots (updated_at DESC);

ALTER TABLE public.organism_runtime_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS organism_runtime_snapshots_workspace_isolation
    ON public.organism_runtime_snapshots;
CREATE POLICY organism_runtime_snapshots_workspace_isolation
    ON public.organism_runtime_snapshots
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_id = auth.uid()::text
        )
    );

REVOKE ALL ON TABLE public.organism_runtime_snapshots FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.organism_runtime_snapshots TO service_role;

COMMENT ON TABLE public.organism_runtime_snapshots IS
    'Latest derived OrganismRuntime state per workspace; contains no action grants or execution leases.';
