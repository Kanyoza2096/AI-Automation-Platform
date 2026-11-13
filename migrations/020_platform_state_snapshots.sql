-- 020_platform_state_snapshots.sql
-- Durable, versioned platform-wide state projection.
-- Writes are serialized through a compare-and-swap RPC; application code must
-- not write snapshot rows directly.

CREATE TABLE IF NOT EXISTS public.platform_state_snapshots (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT platform_state_snapshots_pkey PRIMARY KEY (scope_type, scope_id),
    CONSTRAINT platform_state_snapshots_scope_nonempty
        CHECK (length(btrim(scope_type)) > 0 AND length(btrim(scope_id)) > 0)
);

CREATE INDEX IF NOT EXISTS platform_state_snapshots_updated_at_idx
    ON public.platform_state_snapshots (updated_at DESC);

ALTER TABLE public.platform_state_snapshots ENABLE ROW LEVEL SECURITY;

-- The application reads through its service-role client. Browser roles must
-- never read or mutate platform-wide operational state.
REVOKE ALL ON TABLE public.platform_state_snapshots FROM PUBLIC, anon, authenticated;
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
SET search_path = public, pg_temp
AS $function$
DECLARE
    v_revision BIGINT;
    v_updated_at TIMESTAMPTZ;
    v_schema_version INTEGER;
BEGIN
    IF p_scope_type IS NULL OR length(btrim(p_scope_type)) = 0
       OR p_scope_id IS NULL OR length(btrim(p_scope_id)) = 0 THEN
        RAISE EXCEPTION 'platform_state_scope_required'
            USING ERRCODE = '22023';
    END IF;

    IF p_expected_revision IS NULL OR p_expected_revision < 0 THEN
        RAISE EXCEPTION 'platform_state_expected_revision_invalid'
            USING ERRCODE = '22023';
    END IF;

    IF p_state IS NULL OR jsonb_typeof(p_state) <> 'object' THEN
        RAISE EXCEPTION 'platform_state_payload_must_be_object'
            USING ERRCODE = '22023';
    END IF;

    v_schema_version := COALESCE(NULLIF(p_state->>'schema_version', '')::INTEGER, 1);
    v_updated_at := COALESCE(NULLIF(p_state->>'updated_at', '')::TIMESTAMPTZ, NOW());

    IF v_schema_version < 1 THEN
        RAISE EXCEPTION 'platform_state_schema_version_invalid'
            USING ERRCODE = '22023';
    END IF;

    IF p_expected_revision = 0 THEN
        INSERT INTO public.platform_state_snapshots (
            scope_type, scope_id, revision, schema_version, state, updated_at
        )
        VALUES (
            p_scope_type, p_scope_id, 1, v_schema_version, p_state, v_updated_at
        )
        ON CONFLICT (scope_type, scope_id) DO UPDATE
        SET revision = public.platform_state_snapshots.revision + 1,
            schema_version = EXCLUDED.schema_version,
            state = EXCLUDED.state,
            updated_at = EXCLUDED.updated_at
        WHERE public.platform_state_snapshots.revision = p_expected_revision
        RETURNING revision INTO v_revision;
    ELSE
        UPDATE public.platform_state_snapshots
        SET revision = revision + 1,
            schema_version = v_schema_version,
            state = p_state,
            updated_at = v_updated_at
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
$function$;

REVOKE ALL ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB)
    TO service_role;

COMMENT ON TABLE public.platform_state_snapshots IS
    'Canonical versioned platform-wide operational state projection.';
COMMENT ON FUNCTION public.write_platform_state_snapshot(TEXT, TEXT, BIGINT, JSONB) IS
    'Atomically compare-and-swap a platform state snapshot; raises SQLSTATE 40001 on revision conflict.';
