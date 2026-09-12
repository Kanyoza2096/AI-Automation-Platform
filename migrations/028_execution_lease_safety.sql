-- Execution lease safety. A logical external action has one active executor.
-- Expired in-flight work is quarantined as UNCERTAIN, never blindly retried.
ALTER TABLE external_execution_ledger
  ADD COLUMN IF NOT EXISTS locked_by TEXT,
  ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_external_execution_lease
  ON external_execution_ledger(status, locked_until);

CREATE OR REPLACE FUNCTION claim_external_execution(
  p_execution_id TEXT, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r external_execution_ledger;
BEGIN
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
    SET locked_by=p_worker_id, locked_until=now() + make_interval(secs => GREATEST(30, p_lease_seconds)),
        updated_at=now()
    WHERE execution_id=p_execution_id
    RETURNING * INTO r;
  RETURN NEXT r;
END;
$$;
