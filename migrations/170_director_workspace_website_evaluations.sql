-- 170: Durable, idempotent workspace-scoped Director website evaluations.
-- Evaluation claims are serialized in PostgreSQL so duplicate event deliveries
-- do not trigger concurrent model calls for the same verified context version.

CREATE TABLE IF NOT EXISTS public.director_website_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    integration_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    context_hash TEXT NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'evaluated', 'failed')),
    payload JSONB,
    error_code TEXT,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, integration_id, event_id, context_hash),
    CHECK (
        (status = 'evaluated' AND jsonb_typeof(payload) = 'object' AND lease_until IS NULL)
        OR (status IN ('pending', 'failed') AND payload IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_director_website_evaluations_workspace_updated
    ON public.director_website_evaluations (workspace_id, updated_at DESC);

ALTER TABLE public.director_website_evaluations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_website_evaluations FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.director_website_evaluations TO service_role;

CREATE OR REPLACE FUNCTION public.claim_director_website_evaluation(
    p_evaluation_id TEXT,
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_context_hash TEXT,
    p_lease_seconds INTEGER DEFAULT 120
)
RETURNS TABLE(claimed BOOLEAN, evaluation_status TEXT, evaluation_payload JSONB)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    current_row public.director_website_evaluations%ROWTYPE;
BEGIN
    IF COALESCE(p_evaluation_id, '') = ''
       OR COALESCE(p_workspace_id, '') = ''
       OR COALESCE(p_integration_id, '') = ''
       OR COALESCE(p_event_id, '') = ''
       OR COALESCE(p_context_hash, '') !~ '^[0-9a-f]{64}
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'director_website_evaluation_claim_invalid';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_workspace_id || ':' || p_integration_id || ':' ||
            p_event_id || ':' || p_context_hash,
            0
        )
    );

    SELECT * INTO current_row
      FROM public.director_website_evaluations
     WHERE evaluation_id = p_evaluation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO public.director_website_evaluations (
            evaluation_id, workspace_id, integration_id, event_id,
            context_hash, status, lease_until
        ) VALUES (
            p_evaluation_id, p_workspace_id, p_integration_id, p_event_id,
            p_context_hash, 'pending', now() + make_interval(secs => p_lease_seconds)
        );
        RETURN QUERY SELECT TRUE, 'pending'::TEXT, NULL::JSONB;
        RETURN;
    END IF;

    IF current_row.workspace_id IS DISTINCT FROM p_workspace_id
       OR current_row.integration_id IS DISTINCT FROM p_integration_id
       OR current_row.event_id IS DISTINCT FROM p_event_id
       OR current_row.context_hash IS DISTINCT FROM p_context_hash THEN
        RAISE EXCEPTION 'director_website_evaluation_scope_mismatch';
    END IF;

    IF current_row.status = 'evaluated' THEN
        RETURN QUERY SELECT FALSE, current_row.status, current_row.payload;
        RETURN;
    END IF;

    IF current_row.status = 'pending' AND current_row.lease_until > now() THEN
        RETURN QUERY SELECT FALSE, current_row.status, NULL::JSONB;
        RETURN;
    END IF;

    UPDATE public.director_website_evaluations
       SET status = 'pending',
           error_code = NULL,
           lease_until = now() + make_interval(secs => p_lease_seconds),
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id;

    RETURN QUERY SELECT TRUE, 'pending'::TEXT, NULL::JSONB;
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_director_website_evaluation(
    p_evaluation_id TEXT,
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_context_hash TEXT,
    p_payload JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    IF p_payload IS NULL
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR p_payload->>'evaluation_id' IS DISTINCT FROM p_evaluation_id
       OR p_payload->>'workspace_id' IS DISTINCT FROM p_workspace_id
       OR p_payload->>'integration_id' IS DISTINCT FROM p_integration_id
       OR p_payload->>'event_id' IS DISTINCT FROM p_event_id
       OR p_payload->>'context_hash' IS DISTINCT FROM p_context_hash
       OR p_payload->>'advisory_only' IS DISTINCT FROM 'true'
       OR p_payload->>'authority' IS DISTINCT FROM 'none'
       OR p_payload->>'tasks_created' IS DISTINCT FROM 'false'
       OR p_payload->>'public_publishing_authorized' IS DISTINCT FROM 'false'
       OR p_payload->>'deployment_authorized' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'director_website_evaluation_payload_scope_mismatch';
    END IF;

    UPDATE public.director_website_evaluations
       SET status = 'evaluated',
           payload = p_payload,
           error_code = NULL,
           lease_until = NULL,
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id
       AND workspace_id = p_workspace_id
       AND integration_id = p_integration_id
       AND event_id = p_event_id
       AND context_hash = p_context_hash
       AND status = 'pending';

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END;
$$;

CREATE OR REPLACE FUNCTION public.fail_director_website_evaluation(
    p_evaluation_id TEXT,
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_context_hash TEXT,
    p_error_code TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE public.director_website_evaluations
       SET status = 'failed',
           payload = NULL,
           error_code = left(COALESCE(p_error_code, 'evaluation_failed'), 120),
           lease_until = NULL,
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id
       AND workspace_id = p_workspace_id
       AND integration_id = p_integration_id
       AND event_id = p_event_id
       AND context_hash = p_context_hash
       AND status = 'pending';

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.fail_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.fail_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT)
    TO service_role;

COMMENT ON TABLE public.director_website_evaluations IS
    'Idempotent workspace-scoped advisory evaluations of verified GrowthBridge context; never an execution or publishing authorization.';

       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'director_website_evaluation_claim_invalid';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_workspace_id || ':' || p_integration_id || ':' ||
            p_event_id || ':' || p_context_hash,
            0
        )
    );

    SELECT * INTO current_row
      FROM public.director_website_evaluations
     WHERE evaluation_id = p_evaluation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO public.director_website_evaluations (
            evaluation_id, workspace_id, integration_id, event_id,
            context_hash, status, lease_until
        ) VALUES (
            p_evaluation_id, p_workspace_id, p_integration_id, p_event_id,
            p_context_hash, 'pending', now() + make_interval(secs => p_lease_seconds)
        );
        RETURN QUERY SELECT TRUE, 'pending'::TEXT, NULL::JSONB;
        RETURN;
    END IF;

    IF current_row.workspace_id IS DISTINCT FROM p_workspace_id
       OR current_row.integration_id IS DISTINCT FROM p_integration_id
       OR current_row.event_id IS DISTINCT FROM p_event_id
       OR current_row.context_hash IS DISTINCT FROM p_context_hash THEN
        RAISE EXCEPTION 'director_website_evaluation_scope_mismatch';
    END IF;

    IF current_row.status = 'evaluated' THEN
        RETURN QUERY SELECT FALSE, current_row.status, current_row.payload;
        RETURN;
    END IF;

    IF current_row.status = 'pending' AND current_row.lease_until > now() THEN
        RETURN QUERY SELECT FALSE, current_row.status, NULL::JSONB;
        RETURN;
    END IF;

    UPDATE public.director_website_evaluations
       SET status = 'pending',
           error_code = NULL,
           lease_until = now() + make_interval(secs => p_lease_seconds),
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id;

    RETURN QUERY SELECT TRUE, 'pending'::TEXT, NULL::JSONB;
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_director_website_evaluation(
    p_evaluation_id TEXT,
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_context_hash TEXT,
    p_payload JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    IF p_payload IS NULL
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR p_payload->>'evaluation_id' IS DISTINCT FROM p_evaluation_id
       OR p_payload->>'workspace_id' IS DISTINCT FROM p_workspace_id
       OR p_payload->>'integration_id' IS DISTINCT FROM p_integration_id
       OR p_payload->>'event_id' IS DISTINCT FROM p_event_id
       OR p_payload->>'context_hash' IS DISTINCT FROM p_context_hash
       OR p_payload->>'advisory_only' IS DISTINCT FROM 'true'
       OR p_payload->>'authority' IS DISTINCT FROM 'none'
       OR p_payload->>'tasks_created' IS DISTINCT FROM 'false'
       OR p_payload->>'public_publishing_authorized' IS DISTINCT FROM 'false'
       OR p_payload->>'deployment_authorized' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'director_website_evaluation_payload_scope_mismatch';
    END IF;

    UPDATE public.director_website_evaluations
       SET status = 'evaluated',
           payload = p_payload,
           error_code = NULL,
           lease_until = NULL,
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id
       AND workspace_id = p_workspace_id
       AND integration_id = p_integration_id
       AND event_id = p_event_id
       AND context_hash = p_context_hash
       AND status = 'pending';

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END;
$$;

CREATE OR REPLACE FUNCTION public.fail_director_website_evaluation(
    p_evaluation_id TEXT,
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_context_hash TEXT,
    p_error_code TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE public.director_website_evaluations
       SET status = 'failed',
           payload = NULL,
           error_code = left(COALESCE(p_error_code, 'evaluation_failed'), 120),
           lease_until = NULL,
           updated_at = now()
     WHERE evaluation_id = p_evaluation_id
       AND workspace_id = p_workspace_id
       AND integration_id = p_integration_id
       AND event_id = p_event_id
       AND context_hash = p_context_hash
       AND status = 'pending';

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.fail_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.fail_director_website_evaluation(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT)
    TO service_role;

COMMENT ON TABLE public.director_website_evaluations IS
    'Idempotent workspace-scoped advisory evaluations of verified GrowthBridge context; never an execution or publishing authorization.';
