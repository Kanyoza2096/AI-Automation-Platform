-- Phase 7: lock durable execution RPCs to the backend service role.
-- These functions are SECURITY DEFINER because the backend uses service_role
-- and the ledger must remain authoritative, but they must never be callable by
-- browser/authenticated database sessions directly.

REVOKE EXECUTE ON FUNCTION begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB) TO service_role;

REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

REVOKE EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) TO service_role;

-- Defensive validation inside the privileged RPCs. Invalid lifecycle input
-- should fail at the database boundary, not mutate an execution row.
CREATE OR REPLACE FUNCTION complete_external_execution(
  p_execution_id TEXT, p_status TEXT, p_provider_request_id TEXT DEFAULT NULL,
  p_provider_object_id TEXT DEFAULT NULL, p_error TEXT DEFAULT NULL,
  p_evidence JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_status NOT IN ('succeeded','failed','uncertain','cancelled') THEN
    RAISE EXCEPTION 'Invalid execution terminal status: %', p_status;
  END IF;

  UPDATE external_execution_ledger
  SET status = p_status, attempt = attempt + 1,
      provider_request_id = COALESCE(p_provider_request_id, provider_request_id),
      provider_object_id = COALESCE(p_provider_object_id, provider_object_id),
      error = p_error, evidence = COALESCE(p_evidence, '{}'::jsonb),
      updated_at = now(), locked_by = NULL, locked_until = NULL
  WHERE execution_id = p_execution_id;

  RETURN QUERY SELECT * FROM external_execution_ledger
    WHERE execution_id = p_execution_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

-- Never permit an unbounded lease. The lower bound protects the ambiguity
-- invariant; the upper bound prevents a caller from pinning work indefinitely.
CREATE OR REPLACE FUNCTION claim_external_execution(
  p_execution_id TEXT, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r external_execution_ledger;
BEGIN
  IF p_worker_id IS NULL OR length(trim(p_worker_id)) = 0 THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
  END IF;

  SELECT * INTO r FROM external_execution_ledger
    WHERE execution_id = p_execution_id FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;

  IF r.status IN ('succeeded','failed','cancelled','uncertain') THEN
    RETURN NEXT r; RETURN;
  END IF;

  IF r.locked_by IS NOT NULL AND r.locked_until > now() AND r.locked_by <> p_worker_id THEN
    RETURN NEXT r; RETURN;
  END IF;

  IF r.locked_by IS NOT NULL AND r.locked_until <= now() AND r.status = 'started' THEN
    UPDATE external_execution_ledger
      SET status='uncertain', error='Execution lease expired before completion; reconciliation required',
          updated_at=now(), locked_by=NULL, locked_until=NULL
      WHERE execution_id=p_execution_id
      RETURNING * INTO r;
    RETURN NEXT r; RETURN;
  END IF;

  UPDATE external_execution_ledger
    SET locked_by=p_worker_id,
        locked_until=now() + make_interval(secs => p_lease_seconds),
        updated_at=now()
    WHERE execution_id=p_execution_id
    RETURNING * INTO r;
  RETURN NEXT r;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) TO service_role;
