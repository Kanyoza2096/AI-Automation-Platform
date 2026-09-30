-- V67.3: keep the action-outbox lease alive while a governed handler is running.
-- Without this, a slow provider call could outlive the short outbox lease and a
-- second worker could claim the same intent while the first worker is still live.
CREATE OR REPLACE FUNCTION public.renew_action_outbox_lease(
  p_intent_id TEXT,
  p_workspace_id TEXT,
  p_lease_token TEXT,
  p_lease_seconds INTEGER DEFAULT 120
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_lease_seconds < 30 OR p_lease_seconds > 600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 30 and 600';
  END IF;

  UPDATE public.action_outbox
     SET lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         updated_at = now()
   WHERE intent_id = p_intent_id
     AND workspace_id = p_workspace_id
     AND status = 'processing'
     AND lease_token = p_lease_token
     AND lease_expires_at IS NOT NULL
     AND lease_expires_at > now();
  RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.renew_action_outbox_lease(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.renew_action_outbox_lease(TEXT,TEXT,TEXT,INTEGER) TO service_role;
