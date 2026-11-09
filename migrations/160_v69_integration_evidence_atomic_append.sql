-- V69.13 durable integration evidence append function.
-- The database serializes each workspace/connection hash-chain append and
-- rejects stale previous_hash values. Only service_role may invoke the writer.

CREATE OR REPLACE FUNCTION public.append_integration_evidence(
    p_evidence_id TEXT,
    p_workspace_id TEXT,
    p_connection_id TEXT,
    p_connector_key TEXT,
    p_event_type TEXT,
    p_occurred_at TIMESTAMPTZ,
    p_outcome TEXT,
    p_reason_codes JSONB,
    p_action TEXT,
    p_correlation_id TEXT,
    p_actor TEXT,
    p_provider TEXT,
    p_connector_version TEXT,
    p_payload_fingerprint TEXT,
    p_metadata JSONB,
    p_expected_previous_hash TEXT,
    p_evidence_hash TEXT
)
RETURNS TABLE(evidence_id TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    latest_hash TEXT;
BEGIN
    IF COALESCE(p_evidence_id, '') = ''
       OR COALESCE(p_workspace_id, '') = ''
       OR COALESCE(p_connection_id, '') = ''
       OR COALESCE(p_connector_key, '') = ''
       OR COALESCE(p_event_type, '') = ''
       OR COALESCE(p_outcome, '') = '' THEN
        RAISE EXCEPTION 'integration_evidence_required_field_missing';
    END IF;

    IF COALESCE(p_evidence_hash, '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_payload_fingerprint, '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_expected_previous_hash, '') <> ''
          AND p_expected_previous_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'integration_evidence_hash_invalid';
    END IF;

    IF jsonb_typeof(COALESCE(p_reason_codes, '[]'::jsonb)) <> 'array'
       OR jsonb_typeof(COALESCE(p_metadata, '{}'::jsonb)) <> 'object' THEN
        RAISE EXCEPTION 'integration_evidence_json_shape_invalid';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_workspace_id || ':' || p_connection_id, 0)
    );

    SELECT ie.evidence_hash
      INTO latest_hash
      FROM public.integration_evidence AS ie
     WHERE ie.workspace_id = p_workspace_id
       AND ie.connection_id = p_connection_id
     ORDER BY ie.created_at DESC, ie.evidence_id DESC
     LIMIT 1;

    latest_hash := COALESCE(latest_hash, '');
    IF latest_hash <> COALESCE(p_expected_previous_hash, '') THEN
        RAISE EXCEPTION 'evidence_chain_conflict'
            USING ERRCODE = '40001';
    END IF;

    INSERT INTO public.integration_evidence (
        evidence_id, workspace_id, connection_id, connector_key,
        event_type, occurred_at, outcome, reason_codes, action,
        correlation_id, actor, provider, connector_version,
        payload_fingerprint, metadata, previous_hash, evidence_hash
    ) VALUES (
        p_evidence_id, p_workspace_id, p_connection_id, p_connector_key,
        p_event_type, p_occurred_at, p_outcome,
        COALESCE(p_reason_codes, '[]'::jsonb), COALESCE(p_action, ''),
        p_correlation_id, COALESCE(p_actor, 'system'), COALESCE(p_provider, ''),
        COALESCE(p_connector_version, ''), p_payload_fingerprint,
        COALESCE(p_metadata, '{}'::jsonb), COALESCE(p_expected_previous_hash, ''),
        p_evidence_hash
    );

    RETURN QUERY SELECT p_evidence_id;
END;
$$;

-- Prevent direct table mutation. The SECURITY DEFINER function is owned by
-- the migration owner and is the only supported write path.
REVOKE INSERT, UPDATE, DELETE ON TABLE public.integration_evidence
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.integration_evidence TO service_role;

REVOKE ALL ON FUNCTION public.append_integration_evidence(
    TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, JSONB, TEXT,
    TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.append_integration_evidence(
    TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, JSONB, TEXT,
    TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT
) TO service_role;

CREATE INDEX IF NOT EXISTS idx_integration_evidence_connection_created
    ON public.integration_evidence(workspace_id, connection_id, created_at DESC, evidence_id DESC);
