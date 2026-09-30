-- V67.15: truthful recovery lifecycle closure.
-- A recovery plan is only marked recovered after the exact released outbox
-- intent has reached authoritative terminal success.  This is control-plane
-- bookkeeping; it never executes an external provider.

ALTER TABLE public.autonomous_recovery_plans
  ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'planned'
  CHECK (state IN ('planned','released','recovered','superseded'));

CREATE INDEX IF NOT EXISTS idx_autonomous_recovery_plan_state
  ON public.autonomous_recovery_plans(workspace_id, state, created_at DESC);

CREATE OR REPLACE FUNCTION public.mark_recovery_recovered(
  p_plan_id TEXT,
  p_intent_id TEXT,
  p_workspace_id TEXT,
  p_execution_id TEXT DEFAULT NULL
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_exception_id TEXT;
  v_plan_workspace TEXT;
  v_plan_state TEXT;
  v_outbox_status TEXT;
  v_outbox_plan TEXT;
  v_outbox_execution TEXT;
  v_updated INTEGER;
BEGIN
  SELECT exception_id, workspace_id, state
    INTO v_exception_id, v_plan_workspace, v_plan_state
  FROM public.autonomous_recovery_plans
  WHERE plan_id = p_plan_id
  FOR UPDATE;

  IF NOT FOUND OR v_plan_workspace <> p_workspace_id THEN
    RETURN FALSE;
  END IF;

  SELECT status, recovery_plan_id, execution_id
    INTO v_outbox_status, v_outbox_plan, v_outbox_execution
  FROM public.action_outbox
  WHERE intent_id = p_intent_id
    AND workspace_id = p_workspace_id
  FOR UPDATE;

  IF NOT FOUND OR v_outbox_status <> 'succeeded' OR v_outbox_plan <> p_plan_id THEN
    RETURN FALSE;
  END IF;

  IF p_execution_id IS NOT NULL AND v_outbox_execution IS DISTINCT FROM p_execution_id THEN
    RETURN FALSE;
  END IF;

  UPDATE public.autonomous_recovery_plans
     SET state = 'recovered'
   WHERE plan_id = p_plan_id
     AND workspace_id = p_workspace_id
     AND state IN ('planned','released');

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated <> 1 THEN
    RETURN FALSE;
  END IF;

  UPDATE public.autonomous_exceptions
     SET state = 'recovered', updated_at = now()
   WHERE exception_id = v_exception_id
     AND workspace_id = p_workspace_id
     AND state IN ('recovery_planned','open');

  IF NOT FOUND THEN
    RAISE EXCEPTION 'recovery exception disappeared during closure';
  END IF;

  RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.mark_recovery_recovered(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_recovery_recovered(TEXT,TEXT,TEXT,TEXT) TO service_role;
