-- U2: versioned shared operational-state projection.
-- Subsystem records remain authoritative; this table stores only the bounded
-- platform/workspace readiness projection derived from owner-attributed evidence.
CREATE TABLE IF NOT EXISTS public.platform_state_snapshots (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('platform', 'workspace')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) > 0),
    revision BIGINT NOT NULL CHECK (revision > 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope_type, scope_id),
    CHECK (scope_type <> 'platform' OR scope_id = 'platform')
);

CREATE INDEX IF NOT EXISTS platform_state_snapshots_updated_idx
    ON public.platform_state_snapshots (updated_at DESC);

ALTER TABLE public.platform_state_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.platform_state_snapshots
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.platform_state_snapshots TO service_role;

CREATE OR REPLACE FUNCTION public.write_platform_state_snapshot(
    p_scope_type TEXT,
    p_scope_id TEXT,
    p_expected_revision BIGINT,
    p_state JSONB
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_revision BIGINT;
BEGIN
    IF p_scope_type NOT IN ('platform', 'workspace')
       OR p_scope_id IS NULL OR length(btrim(p_scope_id)) = 0
       OR p_expected_revision IS NULL OR p_expected_revision < 0
       OR p_state IS NULL OR jsonb_typeof(p_state) <> 'object' THEN
        RAISE EXCEPTION 'invalid_platform_state_snapshot'
            USING ERRCODE = '22023';
    END IF;

    IF p_scope_type = 'platform' AND p_scope_id <> 'platform' THEN
        RAISE EXCEPTION 'invalid_platform_state_scope'
            USING ERRCODE = '22023';
    END IF;

    IF p_scope_type = 'workspace' AND NOT EXISTS (
        SELECT 1 FROM public.workspaces
        WHERE id = p_scope_id AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'platform_state_workspace_unavailable'
            USING ERRCODE = '22023';
    END IF;

    -- First write is valid only at revision 0. Subsequent writes update
    -- only an existing row at the exact expected revision. Splitting these
    -- paths prevents a stale writer from recreating a deleted/missing row.
    IF p_expected_revision = 0 THEN
        INSERT INTO public.platform_state_snapshots
            (scope_type, scope_id, revision, schema_version, state, updated_at)
        VALUES
            (p_scope_type, p_scope_id, 1,
             COALESCE(NULLIF(p_state->>'schema_version', '')::INTEGER, 1),
             p_state, NOW())
        ON CONFLICT (scope_type, scope_id) DO NOTHING
        RETURNING revision INTO v_revision;
    ELSE
        UPDATE public.platform_state_snapshots
           SET revision = revision + 1,
               schema_version = COALESCE(NULLIF(p_state->>'schema_version', '')::INTEGER, 1),
               state = p_state,
               updated_at = NOW()
         WHERE scope_type = p_scope_type
           AND scope_id = p_scope_id
           AND revision = p_expected_revision
        RETURNING revision INTO v_revision;
    END IF;

    IF v_revision IS NULL THEN
        RAISE EXCEPTION 'platform_state_revision_conflict'
            USING ERRCODE = '40001';
    END IF;

    RETURN v_revision;
END;
$$;

REVOKE ALL PRIVILEGES ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB)
    TO service_role;

COMMENT ON TABLE public.platform_state_snapshots IS
    'Versioned derived readiness projection. Not a replacement for authoritative subsystem records or action authority.';
COMMENT ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB) IS
    'Atomic compare-and-swap writer for platform state; service_role only.';
