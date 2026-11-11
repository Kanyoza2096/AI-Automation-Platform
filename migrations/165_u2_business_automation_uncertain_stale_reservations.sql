-- U2: stale Business Automation reservations require reconciliation.
--
-- Elapsed time is not proof that an external side effect did not occur.
-- A worker may have completed a provider action and crashed before persisting
-- the parent execution result. Never convert an old "started" row into a new
-- permission to execute. Move it to "uncertain" and require governed
-- reconciliation before any explicit retry/attempt transition.

CREATE OR REPLACE FUNCTION public.reserve_business_automation_execution(p_execution JSONB)
RETURNS SETOF public.business_automation_executions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  r public.business_automation_executions;
  rule_row public.business_automation_rules;
  ws TEXT := NULLIF(TRIM(COALESCE(p_execution->>'workspace_id', '')), '');
  rid TEXT := NULLIF(TRIM(COALESCE(p_execution->>'rule_id', '')), '');
  action_name TEXT := NULLIF(TRIM(COALESCE(p_execution->>'action', '')), '');
  idem TEXT := NULLIF(TRIM(COALESCE(p_execution->>'idempotency_key', '')), '');
  exec_id TEXT := NULLIF(TRIM(COALESCE(p_execution->>'execution_id', '')), '');
  now_ts TIMESTAMPTZ := NOW();
  stale_after INTERVAL := INTERVAL '15 minutes';
BEGIN
  IF ws IS NULL OR rid IS NULL OR action_name IS NULL OR idem IS NULL OR exec_id IS NULL THEN
    RAISE EXCEPTION 'business automation execution reservation requires workspace_id, rule_id, action, execution_id and idempotency_key';
  END IF;

  SELECT * INTO rule_row
    FROM public.business_automation_rules
   WHERE rule_id = rid
   FOR SHARE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'business automation rule not found: %', rid;
  END IF;

  IF rule_row.workspace_id <> ws THEN
    RAISE EXCEPTION 'business automation tenant mismatch for rule %', rid;
  END IF;

  IF rule_row.action <> action_name THEN
    RAISE EXCEPTION 'business automation action mismatch for rule %', rid;
  END IF;

  SELECT * INTO r
    FROM public.business_automation_executions
   WHERE workspace_id = ws
     AND idempotency_key = idem
   FOR UPDATE;

  IF FOUND THEN
    IF r.status = 'started' AND r.started_at < now_ts - stale_after THEN
      UPDATE public.business_automation_executions
         SET status = 'uncertain',
             finished_at = now_ts,
             output = jsonb_build_object(
                 'reconciliation_required', true,
                 'reason', 'stale_started_reservation'
             ),
             error = 'Stale started reservation requires side-effect reconciliation; automatic replay is prohibited.'
       WHERE execution_id = r.execution_id
       RETURNING * INTO r;
    END IF;
    RETURN NEXT r;
    RETURN;
  END IF;

  INSERT INTO public.business_automation_executions(
      execution_id, rule_id, workspace_id, action, status, dry_run,
      started_at, finished_at, output, error, idempotency_key, attempt)
  VALUES(
      exec_id, rid, ws, action_name, 'started',
      COALESCE((p_execution->>'dry_run')::boolean, false),
      COALESCE((p_execution->>'started_at')::timestamptz, now_ts), now_ts,
      jsonb_build_object('_reservation_acquired', true), NULL, idem, 1)
  RETURNING * INTO r;

  RETURN NEXT r;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.reserve_business_automation_execution(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_business_automation_execution(JSONB) TO service_role;
