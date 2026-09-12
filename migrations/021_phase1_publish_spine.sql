-- Phase 1: authoritative durable publish spine.
-- Adds job correlation + atomic outbox leasing.

ALTER TABLE publish_outbox ADD COLUMN IF NOT EXISTS automation_job_id TEXT;
CREATE INDEX IF NOT EXISTS idx_publish_outbox_job ON publish_outbox (automation_job_id);
ALTER TABLE publish_outbox ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
ALTER TABLE publish_outbox ADD COLUMN IF NOT EXISTS locked_by TEXT;

CREATE OR REPLACE FUNCTION claim_publish_outbox(p_limit INTEGER DEFAULT 10, p_lease_seconds INTEGER DEFAULT 300)
RETURNS SETOF publish_outbox
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  WITH candidates AS (
    SELECT id FROM publish_outbox
    WHERE status IN ('pending','failed')
      AND available_at <= NOW()
      AND attempts < max_attempts
      AND (locked_until IS NULL OR locked_until < NOW())
    ORDER BY available_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT GREATEST(1, LEAST(p_limit, 100))
  )
  UPDATE publish_outbox o
  SET status = 'processing',
      attempts = o.attempts + 1,
      locked_until = NOW() + make_interval(secs => GREATEST(30, p_lease_seconds)),
      locked_by = gen_random_uuid()::text,
      updated_at = NOW()
  FROM candidates c
  WHERE o.id = c.id
  RETURNING o.*;
END;
$$;
