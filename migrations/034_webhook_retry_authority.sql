-- Phase 35.5: webhook retry authority. Never mark a delivery completed before
-- its handler succeeds. Processing rows become claimable after their lease expires.

ALTER TABLE IF EXISTS webhook_idempotency
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'processing',
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE IF EXISTS webhook_idempotency
  DROP CONSTRAINT IF EXISTS webhook_idempotency_status_check;
ALTER TABLE IF EXISTS webhook_idempotency
  ADD CONSTRAINT webhook_idempotency_status_check
  CHECK (status IN ('processing', 'completed'));

CREATE OR REPLACE FUNCTION claim_webhook_delivery(
    p_delivery_id TEXT,
    p_workspace_id TEXT DEFAULT NULL,
    p_provider TEXT DEFAULT NULL,
    p_ttl_seconds INTEGER DEFAULT 600
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  affected INTEGER := 0;
BEGIN
  IF p_delivery_id IS NULL OR length(trim(p_delivery_id)) = 0 THEN
    RAISE EXCEPTION 'delivery_id is required';
  END IF;
  IF p_ttl_seconds < 30 OR p_ttl_seconds > 86400 THEN
    RAISE EXCEPTION 'ttl_seconds must be between 30 and 86400';
  END IF;

  INSERT INTO webhook_idempotency(delivery_id, workspace_id, provider, expires_at, status)
  VALUES (trim(p_delivery_id), p_workspace_id, p_provider, NOW() + make_interval(secs => p_ttl_seconds), 'processing')
  ON CONFLICT (delivery_id) DO NOTHING;

  IF FOUND THEN
    RETURN TRUE;
  END IF;

  UPDATE webhook_idempotency
     SET status = 'processing',
         expires_at = NOW() + make_interval(secs => p_ttl_seconds),
         completed_at = NULL,
         updated_at = NOW()
   WHERE delivery_id = trim(p_delivery_id)
     AND status = 'processing'
     AND expires_at < NOW();
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected > 0;
END;
$$;

CREATE OR REPLACE FUNCTION complete_webhook_delivery(p_delivery_id TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE webhook_idempotency
     SET status = 'completed', completed_at = NOW(), expires_at = NOW() + INTERVAL '7 days', updated_at = NOW()
   WHERE delivery_id = trim(p_delivery_id) AND status = 'processing';
  RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_webhook_delivery(TEXT, TEXT, TEXT, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION complete_webhook_delivery(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_webhook_delivery(TEXT, TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION complete_webhook_delivery(TEXT) TO service_role;
