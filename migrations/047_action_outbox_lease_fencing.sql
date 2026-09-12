-- Phase 56: explicit action-outbox ownership fencing.
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS lease_token TEXT;
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_action_outbox_lease ON public.action_outbox(status, lease_expires_at);

CREATE OR REPLACE FUNCTION public.claim_action_outbox(
  p_limit INTEGER DEFAULT 25, p_lease_seconds INTEGER DEFAULT 120, p_worker_id TEXT DEFAULT NULL
) RETURNS SETOF public.action_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_worker TEXT := COALESCE(NULLIF(p_worker_id, ''), 'action:' || gen_random_uuid()::text);
DECLARE v_token TEXT := gen_random_uuid()::text;
BEGIN
  RETURN QUERY
  WITH candidates AS (
    SELECT intent_id FROM public.action_outbox
    WHERE (status = 'pending' AND available_at <= now())
       OR (status = 'processing' AND lease_expires_at IS NOT NULL AND lease_expires_at < now())
    ORDER BY available_at, created_at
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
    FOR UPDATE SKIP LOCKED
  )
  UPDATE public.action_outbox a
     SET status = 'processing', attempts = attempts + 1, updated_at = now(),
         lease_owner = v_worker, lease_token = v_token,
         lease_expires_at = now() + make_interval(secs => GREATEST(p_lease_seconds, 30))
   WHERE a.intent_id IN (SELECT intent_id FROM candidates)
   RETURNING a.*;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_action_outbox(INTEGER, INTEGER, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_action_outbox(INTEGER, INTEGER, TEXT) TO service_role;

CREATE OR REPLACE FUNCTION public.finalize_action_outbox(
  p_intent_id TEXT, p_workspace_id TEXT, p_lease_token TEXT, p_status TEXT,
  p_execution_id TEXT DEFAULT NULL, p_provider_object_id TEXT DEFAULT NULL,
  p_output JSONB DEFAULT '{}'::jsonb, p_error TEXT DEFAULT NULL
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_status NOT IN ('succeeded','failed','reconciliation_required','cancelled') THEN
    RAISE EXCEPTION 'invalid terminal action status';
  END IF;
  UPDATE public.action_outbox
     SET status = p_status, execution_id = p_execution_id, provider_object_id = p_provider_object_id,
         output = COALESCE(p_output, '{}'::jsonb), error = p_error, updated_at = now(),
         lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
   WHERE intent_id = p_intent_id AND workspace_id = p_workspace_id
     AND status = 'processing' AND lease_token = p_lease_token;
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.finalize_action_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_action_outbox(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT) TO service_role;
