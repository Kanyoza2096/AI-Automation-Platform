-- Phase 6: distributed failure-storm hardening.
-- A worker may only finalize an outbox item while it owns the lease.
-- Ambiguous/lost-lease outcomes are not silently retried. Expired leases are
-- quarantined as uncertain because an external side effect may already exist.

CREATE OR REPLACE FUNCTION claim_publish_outbox(p_limit INTEGER DEFAULT 10, p_lease_seconds INTEGER DEFAULT 300)
RETURNS SETOF publish_outbox LANGUAGE plpgsql AS $$
BEGIN
  UPDATE publish_outbox
     SET status='uncertain', last_error='Worker lease expired; reconcile before retry',
         locked_until=NULL, locked_by=NULL, updated_at=NOW()
   WHERE status='processing' AND locked_until IS NOT NULL AND locked_until < NOW();

  RETURN QUERY
  WITH candidates AS (
    SELECT id FROM publish_outbox
     WHERE status='pending' AND available_at <= NOW() AND attempts < max_attempts
     ORDER BY available_at ASC FOR UPDATE SKIP LOCKED
     LIMIT GREATEST(1, LEAST(p_limit,100))
  )
  UPDATE publish_outbox o
     SET status='processing', attempts=o.attempts+1,
         locked_until=NOW()+make_interval(secs=>GREATEST(30,p_lease_seconds)),
         locked_by=gen_random_uuid()::text, updated_at=NOW()
    FROM candidates c WHERE o.id=c.id RETURNING o.*;
END; $$;

CREATE OR REPLACE FUNCTION complete_publish_outbox(
  p_id TEXT, p_locked_by TEXT, p_status TEXT, p_error TEXT DEFAULT NULL,
  p_platform_post_id TEXT DEFAULT NULL, p_retry_delay_seconds INTEGER DEFAULT 5
) RETURNS TABLE(updated BOOLEAN) LANGUAGE plpgsql AS $$
BEGIN
  IF p_status NOT IN ('published', 'failed', 'dead', 'uncertain') THEN
    RAISE EXCEPTION 'Invalid publish completion status: %', p_status;
  END IF;

  RETURN QUERY
  UPDATE publish_outbox
     SET status = p_status,
         platform_post_id = COALESCE(p_platform_post_id, platform_post_id),
         published_at = CASE WHEN p_status = 'published' THEN NOW() ELSE published_at END,
         last_error = CASE WHEN p_status = 'published' THEN NULL ELSE LEFT(COALESCE(p_error,''),500) END,
         available_at = CASE WHEN p_status = 'failed' THEN NOW() + make_interval(secs => GREATEST(1,p_retry_delay_seconds)) ELSE available_at END,
         locked_until = NULL, locked_by = NULL, updated_at = NOW()
   WHERE id = p_id AND status = 'processing' AND locked_by = p_locked_by
  RETURNING TRUE;
  IF NOT FOUND THEN RETURN QUERY SELECT FALSE; END IF;
END; $$;

CREATE OR REPLACE FUNCTION fail_publish_outbox(
  p_id TEXT, p_locked_by TEXT, p_status TEXT, p_error TEXT DEFAULT NULL,
  p_platform_post_id TEXT DEFAULT NULL, p_retry_delay_seconds INTEGER DEFAULT 5
) RETURNS TABLE(updated BOOLEAN) LANGUAGE plpgsql AS $$
DECLARE
  next_status TEXT;
BEGIN
  next_status := CASE WHEN p_status = 'dead' THEN 'dead' ELSE 'failed' END;
  RETURN QUERY
  UPDATE publish_outbox
     SET status = next_status, last_error = LEFT(COALESCE(p_error,''),500),
         available_at = CASE WHEN next_status='failed' THEN NOW() + make_interval(secs => GREATEST(1,p_retry_delay_seconds)) ELSE available_at END,
         locked_until = NULL, locked_by = NULL, updated_at = NOW()
   WHERE id = p_id AND status = 'processing' AND locked_by = p_locked_by
  RETURNING TRUE;
  IF NOT FOUND THEN RETURN QUERY SELECT FALSE; END IF;
END; $$;
