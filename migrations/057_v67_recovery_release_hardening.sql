-- V67 hardening: recovery release is valid only for a matching persisted AUTO_RECOVER plan.
-- The database remains the final race-safe guard before an intent returns to pending.
CREATE OR REPLACE FUNCTION public.release_action_outbox_for_recovery(
  p_intent_id TEXT, p_workspace_id TEXT, p_plan_id TEXT, p_delay_seconds INTEGER DEFAULT 0
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_disposition TEXT;
  v_authority TEXT;
  v_plan_workspace TEXT;
  v_exception_workspace TEXT;
  v_exception_id TEXT;
  v_exception_intent TEXT;
  v_max_attempts INTEGER;
  v_attempts INTEGER;
BEGIN
  IF p_delay_seconds < 0 OR p_delay_seconds > 3600 THEN
    RAISE EXCEPTION 'invalid recovery delay';
  END IF;

  SELECT disposition, authority, workspace_id, exception_id, max_attempts
    INTO v_disposition, v_authority, v_plan_workspace, v_exception_id, v_max_attempts
  FROM public.autonomous_recovery_plans
  WHERE plan_id = p_plan_id;

  IF NOT FOUND OR v_disposition <> 'auto_recover' OR v_authority <> 'none'
     OR v_plan_workspace <> p_workspace_id THEN
    RETURN FALSE;
  END IF;

  SELECT workspace_id, intent_id
    INTO v_exception_workspace, v_exception_intent
  FROM public.autonomous_exceptions
  WHERE exception_id = v_exception_id;

  IF NOT FOUND OR v_exception_workspace <> p_workspace_id OR v_exception_intent <> p_intent_id THEN
    RETURN FALSE;
  END IF;

  SELECT attempts INTO v_attempts
  FROM public.action_outbox
  WHERE intent_id = p_intent_id AND workspace_id = p_workspace_id AND status = 'failed'
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN FALSE;
  END IF;

  IF v_max_attempts <= 0 OR v_attempts >= v_max_attempts THEN
    RETURN FALSE;
  END IF;

  UPDATE public.action_outbox
     SET status = 'pending',
         available_at = now() + make_interval(secs => p_delay_seconds),
         recovery_plan_id = p_plan_id,
         updated_at = now(),
         lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
   WHERE intent_id = p_intent_id
     AND workspace_id = p_workspace_id
     AND status = 'failed';
  RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) TO service_role;
