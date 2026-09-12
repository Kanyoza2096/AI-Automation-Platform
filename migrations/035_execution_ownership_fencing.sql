-- Phase 35.7: execution ownership fencing.
-- Completion must be performed by the worker that owns the live lease.
-- This prevents a stale/duplicate worker from committing a receipt after
-- another worker has taken over (or after the lease was quarantined).

DROP FUNCTION IF EXISTS complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB);

CREATE OR REPLACE FUNCTION complete_external_execution(
  p_execution_id TEXT,
  p_worker_id TEXT,
  p_status TEXT,
  p_provider_request_id TEXT DEFAULT NULL,
  p_provider_object_id TEXT DEFAULT NULL,
  p_error TEXT DEFAULT NULL,
  p_evidence JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r external_execution_ledger;
BEGIN
  IF p_worker_id IS NULL OR length(trim(p_worker_id)) = 0 THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF p_status NOT IN ('succeeded','failed','uncertain','cancelled') THEN
    RAISE EXCEPTION 'Invalid execution terminal status: %', p_status;
  END IF;

  SELECT * INTO r FROM external_execution_ledger
    WHERE execution_id = p_execution_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN;
  END IF;

  IF r.status IN ('succeeded','failed','cancelled','uncertain') THEN
    RETURN NEXT r;
    RETURN;
  END IF;

  IF r.locked_by IS DISTINCT FROM p_worker_id OR r.locked_until IS NULL OR r.locked_until <= now() THEN
    RAISE EXCEPTION 'Execution ownership lost or lease expired for %', p_execution_id;
  END IF;

  UPDATE external_execution_ledger
  SET status = p_status,
      attempt = attempt + 1,
      provider_request_id = COALESCE(p_provider_request_id, provider_request_id),
      provider_object_id = COALESCE(p_provider_object_id, provider_object_id),
      error = p_error,
      evidence = COALESCE(p_evidence, '{}'::jsonb),
      updated_at = now(),
      locked_by = NULL,
      locked_until = NULL
  WHERE execution_id = p_execution_id
  RETURNING * INTO r;

  RETURN NEXT r;
END;
$$;

REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

-- The earlier migration used a stale UUID signature. Keep the final schema
-- self-contained and prevent accidental execution grants on that signature.
DO $$
BEGIN
  BEGIN
    REVOKE EXECUTE ON FUNCTION begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC, anon, authenticated, service_role;
  EXCEPTION WHEN undefined_function THEN
    NULL;
  END;
END $$;
