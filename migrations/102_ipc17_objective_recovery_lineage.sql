-- IPC-17: durable objective/cycle lineage for autonomous recovery.
-- Recovery remains authority-free; lineage is evidence, not permission.
ALTER TABLE public.autonomous_exceptions
  ADD COLUMN IF NOT EXISTS objective_id TEXT,
  ADD COLUMN IF NOT EXISTS cycle_id TEXT,
  ADD COLUMN IF NOT EXISTS run_id TEXT;

ALTER TABLE public.autonomous_recovery_plans
  ADD COLUMN IF NOT EXISTS objective_id TEXT,
  ADD COLUMN IF NOT EXISTS cycle_id TEXT,
  ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_autonomous_exceptions_objective
  ON public.autonomous_exceptions(workspace_id, objective_id, cycle_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_recovery_plans_objective
  ON public.autonomous_recovery_plans(workspace_id, objective_id, cycle_id, created_at DESC);

-- Keep service-role-only mutation posture. These columns are lineage evidence;
-- they do not authorize execution or objective lifecycle changes.

-- Re-assert the release race boundary with objective lineage. A recovery plan
-- cannot be released if its durable objective/cycle/run evidence disagrees with
-- the originating exception. Legacy non-objective recovery remains supported.
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
  v_plan_objective TEXT;
  v_plan_cycle TEXT;
  v_plan_run TEXT;
  v_exception_objective TEXT;
  v_exception_cycle TEXT;
  v_exception_run TEXT;
  v_updated INTEGER;
BEGIN
  IF p_delay_seconds < 0 OR p_delay_seconds > 3600 THEN
    RAISE EXCEPTION 'invalid recovery delay';
  END IF;

  SELECT disposition, authority, workspace_id, exception_id, max_attempts,
         objective_id, cycle_id, run_id
    INTO v_disposition, v_authority, v_plan_workspace, v_exception_id, v_max_attempts,
         v_plan_objective, v_plan_cycle, v_plan_run
  FROM public.autonomous_recovery_plans
  WHERE plan_id = p_plan_id
  FOR UPDATE;

  IF NOT FOUND OR v_disposition <> 'auto_recover' OR v_authority <> 'none'
     OR v_plan_workspace <> p_workspace_id THEN
    RETURN FALSE;
  END IF;

  SELECT workspace_id, intent_id, state, objective_id, cycle_id, run_id
    INTO v_exception_workspace, v_exception_intent, v_exception_state,
         v_exception_objective, v_exception_cycle, v_exception_run
  FROM public.autonomous_exceptions
  WHERE exception_id = v_exception_id
  FOR UPDATE;

  IF NOT FOUND OR v_exception_workspace <> p_workspace_id
     OR v_exception_intent <> p_intent_id THEN
    RETURN FALSE;
  END IF;

  IF v_plan_objective IS DISTINCT FROM v_exception_objective
     OR v_plan_cycle IS DISTINCT FROM v_exception_cycle
     OR v_plan_run IS DISTINCT FROM v_exception_run THEN
    RETURN FALSE;
  END IF;

  SELECT attempts INTO v_attempts
  FROM public.action_outbox
  WHERE intent_id = p_intent_id
    AND workspace_id = p_workspace_id
    AND status = 'failed'
  FOR UPDATE;

  IF NOT FOUND OR v_max_attempts <= 0 OR v_attempts >= v_max_attempts THEN
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
