-- V67.5: recovery-release race hardening.
--
-- The release boundary is a transactional state transition, not a best-effort
-- UPDATE. A recovery plan may be observed by multiple workers/coordinators, but
-- only one transaction may move the failed outbox row back to pending.
--
-- The exception state is advanced in the same transaction so the control plane
-- cannot report recovery_planned after a release that was rolled back.
CREATE OR REPLACE FUNCTION public.release_action_outbox_for_recovery(
  p_intent_id TEXT, p_workspace_id TEXT, p_plan_id TEXT, p_delay_seconds INTEGER DEFAULT 0
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_disposition TEXT;
  v_authority TEXT;
  v_plan_workspace TEXT;
  v_exception_id TEXT;
  v_exception_workspace TEXT;
  v_exception_intent TEXT;
  v_exception_state TEXT;
  v_max_attempts INTEGER;
  v_attempts INTEGER;
  v_updated INTEGER;
BEGIN
  IF p_delay_seconds < 0 OR p_delay_seconds > 3600 THEN
    RAISE EXCEPTION 'invalid recovery delay';
  END IF;

  -- Serialize competing recovery coordinators for the same plan. This is
  -- deliberately a row lock: deterministic plan IDs make concurrent planners
  -- converge on the same control-plane object.
  SELECT disposition, authority, workspace_id, exception_id, max_attempts
    INTO v_disposition, v_authority, v_plan_workspace, v_exception_id, v_max_attempts
  FROM public.autonomous_recovery_plans
  WHERE plan_id = p_plan_id
  FOR UPDATE;

  IF NOT FOUND OR v_disposition <> 'auto_recover' OR v_authority <> 'none'
     OR v_plan_workspace <> p_workspace_id THEN
    RETURN FALSE;
  END IF;

  SELECT workspace_id, intent_id, state
    INTO v_exception_workspace, v_exception_intent, v_exception_state
  FROM public.autonomous_exceptions
  WHERE exception_id = v_exception_id
  FOR UPDATE;

  IF NOT FOUND OR v_exception_workspace <> p_workspace_id
     OR v_exception_intent <> p_intent_id THEN
    RETURN FALSE;
  END IF;

  -- A release may be repeated by a retried coordinator, but only while the
  -- outbox row is still failed. Once another worker has claimed it, the second
  -- release must lose the race rather than resetting live work.
  SELECT attempts INTO v_attempts
  FROM public.action_outbox
  WHERE intent_id = p_intent_id
    AND workspace_id = p_workspace_id
    AND status = 'failed'
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
         lease_owner = NULL,
         lease_token = NULL,
         lease_expires_at = NULL
   WHERE intent_id = p_intent_id
     AND workspace_id = p_workspace_id
     AND status = 'failed';

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated <> 1 THEN
    RETURN FALSE;
  END IF;

  UPDATE public.autonomous_exceptions
     SET state = 'recovery_planned', updated_at = now()
   WHERE exception_id = v_exception_id
     AND workspace_id = p_workspace_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'recovery exception disappeared during release';
  END IF;

  RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) TO service_role;
