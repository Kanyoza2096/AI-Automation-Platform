-- 168: Serialize GrowthBridge website-event observation appends.
-- A process-local lock is insufficient when multiple KDO workers consume the
-- same event. This RPC makes the idempotency check and insert one transaction.

CREATE OR REPLACE FUNCTION public.record_director_website_event_observation(
    p_workspace_id TEXT,
    p_integration_id TEXT,
    p_event_id TEXT,
    p_payload JSONB,
    p_timestamp TIMESTAMPTZ
)
RETURNS TABLE(observation_id BIGINT, inserted BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    existing_observation_id BIGINT;
    inserted_observation_id BIGINT;
BEGIN
    IF COALESCE(p_workspace_id, '') = ''
       OR COALESCE(p_integration_id, '') = ''
       OR COALESCE(p_event_id, '') = '' THEN
        RAISE EXCEPTION 'director_website_observation_scope_required';
    END IF;

    IF jsonb_typeof(p_payload) <> 'object'
       OR p_payload->>'source' <> 'website_integration_events'
       OR p_payload #>> '{metrics,workspace_id}' <> p_workspace_id
       OR p_payload #>> '{metrics,integration_id}' <> p_integration_id
       OR p_payload #>> '{metrics,website_event_id}' <> p_event_id THEN
        RAISE EXCEPTION 'director_website_observation_payload_scope_mismatch';
    END IF;

    -- Hash collisions only serialize unrelated events; they cannot weaken
    -- correctness. The lock is transaction-scoped and needs no new service.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_workspace_id || ':' || p_integration_id || ':' || p_event_id,
            0
        )
    );

    SELECT observation.id
      INTO existing_observation_id
      FROM public.director_observations AS observation
     WHERE observation.workspace_id = p_workspace_id
       AND observation.payload->>'source' = 'website_integration_events'
       AND observation.payload #>> '{metrics,integration_id}' = p_integration_id
       AND observation.payload #>> '{metrics,website_event_id}' = p_event_id
     ORDER BY observation.id
     LIMIT 1;

    IF existing_observation_id IS NOT NULL THEN
        RETURN QUERY SELECT existing_observation_id, FALSE;
        RETURN;
    END IF;

    INSERT INTO public.director_observations (payload, timestamp, workspace_id)
    VALUES (p_payload, COALESCE(p_timestamp, now()), p_workspace_id)
    RETURNING id INTO inserted_observation_id;

    RETURN QUERY SELECT inserted_observation_id, TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.record_director_website_event_observation(
    TEXT, TEXT, TEXT, JSONB, TIMESTAMPTZ
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_director_website_event_observation(
    TEXT, TEXT, TEXT, JSONB, TIMESTAMPTZ
) TO service_role;
