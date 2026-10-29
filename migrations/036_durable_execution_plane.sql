-- Phase 36.0: durable execution-plane authority.
--
-- Idempotency is tenant-scoped. A caller-supplied key may legitimately be
-- reused in two workspaces without causing cross-tenant replay/collision.
-- The execution ledger remains backend-authoritative; RLS protects direct
-- tenant reads/writes for authenticated database sessions.

ALTER TABLE IF EXISTS external_execution_ledger
  DROP CONSTRAINT IF EXISTS external_execution_ledger_idempotency_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_external_execution_workspace_idempotency
  ON external_execution_ledger (workspace_id, idempotency_key);

CREATE OR REPLACE FUNCTION begin_external_execution(
  p_workspace_id TEXT,
  p_action TEXT,
  p_idempotency_key TEXT,
  p_provider TEXT DEFAULT NULL,
  p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_workspace_id IS NULL OR length(trim(p_workspace_id)) = 0 THEN
    RAISE EXCEPTION 'workspace_id is required';
  END IF;
  IF p_action IS NULL OR length(trim(p_action)) = 0 THEN
    RAISE EXCEPTION 'action is required';
  END IF;
  IF p_idempotency_key IS NULL OR length(trim(p_idempotency_key)) = 0 THEN
    RAISE EXCEPTION 'idempotency_key is required';
  END IF;

  INSERT INTO external_execution_ledger
    (execution_id, workspace_id, action, idempotency_key, provider, evidence)
  VALUES
    ('exe_' || encode(gen_random_bytes(12), 'hex'), trim(p_workspace_id),
     trim(p_action), trim(p_idempotency_key), p_provider,
     COALESCE(p_metadata, '{}'::jsonb))
  ON CONFLICT (workspace_id, idempotency_key) DO NOTHING;

  RETURN QUERY
    SELECT * FROM external_execution_ledger
     WHERE workspace_id = trim(p_workspace_id)
       AND idempotency_key = trim(p_idempotency_key);
END;
$$;

REVOKE EXECUTE ON FUNCTION begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB)
  TO service_role;

-- Long-running providers need a bounded heartbeat. Renewal is ownership
-- fenced and cannot revive a lease after it has already expired.
CREATE OR REPLACE FUNCTION renew_external_execution(
  p_execution_id TEXT,
  p_worker_id TEXT,
  p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_worker_id IS NULL OR length(trim(p_worker_id)) = 0 THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
  END IF;

  RETURN QUERY
  UPDATE external_execution_ledger
     SET locked_until = now() + make_interval(secs => p_lease_seconds),
         updated_at = now()
   WHERE execution_id = p_execution_id
     AND status = 'started'
     AND locked_by = p_worker_id
     AND locked_until > now()
  RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION renew_external_execution(TEXT, TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION renew_external_execution(TEXT, TEXT, INTEGER)
  TO service_role;

-- Explicitly forbid direct client execution of the durable execution RPCs.
REVOKE EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_external_execution(TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;
