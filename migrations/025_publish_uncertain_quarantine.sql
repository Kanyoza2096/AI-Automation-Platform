-- Preserve ambiguous external side effects as UNCERTAIN.
-- Never convert an uncertain publish into a retryable failed state automatically.

CREATE OR REPLACE FUNCTION quarantine_publish_outbox(
  p_id TEXT, p_locked_by TEXT, p_status TEXT DEFAULT 'uncertain',
  p_error TEXT DEFAULT NULL, p_platform_post_id TEXT DEFAULT NULL
) RETURNS TABLE(updated BOOLEAN) LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  UPDATE publish_outbox
     SET status = 'uncertain',
         last_error = LEFT(COALESCE(p_error, 'External side effect outcome is ambiguous'), 500),
         platform_post_id = COALESCE(p_platform_post_id, platform_post_id),
         locked_until = NULL, locked_by = NULL, updated_at = NOW()
   WHERE id = p_id AND status = 'processing' AND locked_by = p_locked_by
  RETURNING TRUE;
  IF NOT FOUND THEN RETURN QUERY SELECT FALSE; END IF;
END; $$;
