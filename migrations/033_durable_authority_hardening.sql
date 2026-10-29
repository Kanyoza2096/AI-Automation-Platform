-- Phase 35.4: close durable-authority schema/RPC gaps found during runtime audit.
--
-- The application models workspace IDs as TEXT throughout the platform. The
-- original external execution ledger migration declared workspace_id UUID,
-- which made its membership RLS predicate incompatible with workspace_members
-- (workspace_id/user_id are TEXT). Normalize the ledger to the platform's
-- canonical tenant identifier type before any real production bootstrap.

ALTER TABLE IF EXISTS external_execution_ledger
  ALTER COLUMN workspace_id TYPE TEXT USING workspace_id::text;

DROP POLICY IF EXISTS execution_ledger_workspace_isolation ON external_execution_ledger;
CREATE POLICY execution_ledger_workspace_isolation ON external_execution_ledger
  FOR ALL
  USING (
    workspace_id IN (
      SELECT workspace_id
      FROM workspace_members
      WHERE user_id = auth.uid()::text
        AND accepted_at IS NOT NULL
    )
  )
  WITH CHECK (
    workspace_id IN (
      SELECT workspace_id
      FROM workspace_members
      WHERE user_id = auth.uid()::text
        AND accepted_at IS NOT NULL
    )
  );

-- The Python service passes workspace IDs as strings. Replace the UUID-typed
-- begin RPC with the canonical TEXT signature. Dropping the old overload is
-- necessary because PostgreSQL treats UUID and TEXT signatures as distinct.
DROP FUNCTION IF EXISTS begin_external_execution(UUID, TEXT, TEXT, TEXT, JSONB);

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
    ('exe_' || encode(gen_random_bytes(12), 'hex'), p_workspace_id,
     p_action, p_idempotency_key, p_provider, COALESCE(p_metadata, '{}'::jsonb))
  ON CONFLICT (idempotency_key) DO NOTHING;

  RETURN QUERY
    SELECT * FROM external_execution_ledger
    WHERE idempotency_key = p_idempotency_key;
END;
$$;

REVOKE EXECUTE ON FUNCTION begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION begin_external_execution(TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

-- Reassert completion/claim authorization and bounded lease semantics. The
-- outbox drainer must be backend-only and a caller must never be able to pin
-- a row indefinitely with an arbitrarily long lease.
CREATE OR REPLACE FUNCTION claim_publish_outbox(
  p_limit INTEGER DEFAULT 10,
  p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF publish_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'limit must be between 1 and 100';
  END IF;
  IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
  END IF;

  UPDATE publish_outbox
     SET status = 'uncertain',
         last_error = 'Worker lease expired; reconcile before retry',
         locked_until = NULL,
         locked_by = NULL,
         updated_at = NOW()
   WHERE status = 'processing'
     AND locked_until IS NOT NULL
     AND locked_until < NOW();

  RETURN QUERY
  WITH candidates AS (
    SELECT id
      FROM publish_outbox
     WHERE status = 'pending'
       AND available_at <= NOW()
       AND attempts < max_attempts
     ORDER BY available_at ASC
     FOR UPDATE SKIP LOCKED
     LIMIT p_limit
  )
  UPDATE publish_outbox o
     SET status = 'processing',
         attempts = o.attempts + 1,
         locked_until = NOW() + make_interval(secs => p_lease_seconds),
         locked_by = gen_random_uuid()::text,
         updated_at = NOW()
    FROM candidates c
   WHERE o.id = c.id
  RETURNING o.*;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_publish_outbox(INTEGER, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION claim_publish_outbox(INTEGER, INTEGER) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_publish_outbox(INTEGER, INTEGER) TO service_role;

REVOKE EXECUTE ON FUNCTION complete_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION complete_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) TO service_role;

REVOKE EXECUTE ON FUNCTION quarantine_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION quarantine_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION quarantine_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT) TO service_role;

REVOKE EXECUTE ON FUNCTION fail_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION fail_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION fail_publish_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) TO service_role;
