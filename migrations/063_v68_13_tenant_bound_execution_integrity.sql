-- V68.13: tenant-bound Business Automation execution integrity.
-- Prevents malformed or cross-workspace execution reservations from entering the
-- durable execution ledger, even when called through the service-role RPC.

CREATE OR REPLACE FUNCTION public.reserve_business_automation_execution(p_execution JSONB)
RETURNS SETOF public.business_automation_executions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
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
         SET started_at = now_ts, finished_at = now_ts, attempt = r.attempt + 1,
             output = jsonb_build_object('_reservation_acquired', true), error = NULL
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

-- Defense in depth for direct service-role writes: the ledger row must always
-- belong to the same workspace and action as its referenced automation rule.
CREATE OR REPLACE FUNCTION public.validate_business_automation_execution_tenant()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE rule_ws TEXT; rule_action TEXT;
BEGIN
  SELECT workspace_id, action INTO rule_ws, rule_action
    FROM public.business_automation_rules WHERE rule_id = NEW.rule_id;
  IF rule_ws IS NULL THEN RAISE EXCEPTION 'business automation execution references unknown rule %', NEW.rule_id; END IF;
  IF rule_ws <> NEW.workspace_id THEN RAISE EXCEPTION 'business automation execution workspace mismatch'; END IF;
  IF rule_action <> NEW.action THEN RAISE EXCEPTION 'business automation execution action mismatch'; END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_business_automation_execution_tenant ON public.business_automation_executions;
CREATE TRIGGER trg_validate_business_automation_execution_tenant
BEFORE INSERT OR UPDATE OF rule_id, workspace_id, action
ON public.business_automation_executions
FOR EACH ROW EXECUTE FUNCTION public.validate_business_automation_execution_tenant();
