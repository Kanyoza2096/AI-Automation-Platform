-- U2: atomically persist tenant-bound connector health and its evidence record.
-- The evidence append and health observation share one transaction; a failed
-- health-row insert rolls back the evidence append as well.

CREATE OR REPLACE FUNCTION public.record_connector_health_observation(
    p_evidence_id TEXT,
    p_workspace_id TEXT,
    p_connection_id TEXT,
    p_connector_key TEXT,
    p_observed_at TIMESTAMPTZ,
    p_health_state TEXT,
    p_drift JSONB,
    p_provider_status_code INTEGER,
    p_latency_ms INTEGER,
    p_reason_codes JSONB,
    p_actor TEXT,
    p_provider TEXT,
    p_connector_version TEXT,
    p_payload_fingerprint TEXT,
    p_metadata JSONB,
    p_expected_previous_hash TEXT,
    p_evidence_hash TEXT
)
RETURNS TABLE(evidence_id TEXT, observation_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    appended_evidence_id TEXT;
    inserted_observation_id UUID;
BEGIN
    IF COALESCE(p_health_state, '') NOT IN ('healthy', 'degraded', 'unavailable', 'unknown') THEN
        RAISE EXCEPTION 'connector_health_state_invalid';
    END IF;
    IF p_latency_ms IS NOT NULL AND p_latency_ms < 0 THEN
        RAISE EXCEPTION 'connector_health_latency_invalid';
    END IF;
    IF jsonb_typeof(COALESCE(p_drift, '[]'::jsonb)) <> 'array' THEN
        RAISE EXCEPTION 'connector_health_drift_shape_invalid';
    END IF;

    SELECT appended.evidence_id
      INTO appended_evidence_id
      FROM public.append_integration_evidence(
          p_evidence_id,
          p_workspace_id,
          p_connection_id,
          p_connector_key,
          'connector.health.observed',
          p_observed_at,
          p_health_state,
          COALESCE(p_reason_codes, '[]'::jsonb),
          '',
          NULL,
          COALESCE(p_actor, 'system'),
          COALESCE(p_provider, ''),
          COALESCE(p_connector_version, ''),
          p_payload_fingerprint,
          COALESCE(p_metadata, '{}'::jsonb),
          COALESCE(p_expected_previous_hash, ''),
          p_evidence_hash
      ) AS appended;

    INSERT INTO public.connector_health_observations (
        workspace_id, connection_id, connector_key, observed_at,
        health_state, drift, provider_status_code, latency_ms, evidence_ref
    ) VALUES (
        p_workspace_id, p_connection_id, p_connector_key, p_observed_at,
        p_health_state, COALESCE(p_drift, '[]'::jsonb),
        p_provider_status_code, p_latency_ms, appended_evidence_id
    )
    RETURNING id INTO inserted_observation_id;

    RETURN QUERY SELECT appended_evidence_id, inserted_observation_id;
END;
$$;

-- Health observations are written only through the atomic RPC.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.connector_health_observations
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.connector_health_observations TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.record_connector_health_observation(
    TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, JSONB, INTEGER, INTEGER,
    JSONB, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_connector_health_observation(
    TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, JSONB, INTEGER, INTEGER,
    JSONB, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT
) TO service_role;
