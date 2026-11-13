-- Durable, workspace-scoped snapshots for OrganismRuntime recovery.
-- The application uses the backend service-role client only. Tenant/browser
-- roles must never read or mutate these internal cognitive-state snapshots.

CREATE TABLE IF NOT EXISTS public.organism_runtime_snapshots (
    workspace_id text PRIMARY KEY
        REFERENCES public.workspaces(id) ON DELETE CASCADE,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    state_sha256 text NOT NULL
        CHECK (state_sha256 ~ '^[0-9a-f]{64}$'),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.organism_runtime_snapshots IS
    'Latest derived OrganismRuntime state per workspace; never stores action grants, approvals, credentials, or execution authority.';

ALTER TABLE public.organism_runtime_snapshots ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.organism_runtime_snapshots
    FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.organism_runtime_snapshots
    TO service_role;
