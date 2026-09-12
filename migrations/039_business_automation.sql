-- Durable internal business automation.
CREATE TABLE IF NOT EXISTS business_automation_rules (
  rule_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  condition JSONB NOT NULL DEFAULT '{}'::jsonb,
  action TEXT NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  risk TEXT NOT NULL DEFAULT 'low' CHECK (risk IN ('low','medium','high','critical')),
  schedule_at TIMESTAMPTZ,
  interval_seconds INTEGER CHECK (interval_seconds IS NULL OR interval_seconds BETWEEN 60 AND 2592000),
  next_run_at TIMESTAMPTZ,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_business_automation_due ON business_automation_rules(enabled, next_run_at) WHERE next_run_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_business_automation_workspace ON business_automation_rules(workspace_id, created_at DESC);
ALTER TABLE business_automation_rules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_automation_rules_workspace_select ON business_automation_rules;
CREATE POLICY business_automation_rules_workspace_select ON business_automation_rules FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS business_automation_rules_workspace_insert ON business_automation_rules;
CREATE POLICY business_automation_rules_workspace_insert ON business_automation_rules FOR INSERT WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS business_automation_rules_workspace_update ON business_automation_rules;
CREATE POLICY business_automation_rules_workspace_update ON business_automation_rules FOR UPDATE USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text)) WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS business_automation_executions (
  execution_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES business_automation_rules(rule_id) ON DELETE CASCADE,
  workspace_id TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  dry_run BOOLEAN NOT NULL DEFAULT FALSE,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  output JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  idempotency_key TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_business_automation_exec_workspace ON business_automation_executions(workspace_id, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_automation_exec_idempotency ON business_automation_executions(workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
ALTER TABLE business_automation_executions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_automation_exec_workspace_select ON business_automation_executions;
CREATE POLICY business_automation_exec_workspace_select ON business_automation_executions FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

-- Claim due rules with row locking. A worker receives only its claimed rows.
CREATE OR REPLACE FUNCTION claim_due_business_automation_rules(p_limit INTEGER DEFAULT 20)
RETURNS SETOF business_automation_rules
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
  WITH due AS (
    SELECT rule_id FROM business_automation_rules
    WHERE enabled = TRUE AND next_run_at IS NOT NULL AND next_run_at <= now()
    ORDER BY next_run_at ASC LIMIT GREATEST(1, LEAST(p_limit, 100)) FOR UPDATE SKIP LOCKED
  )
  UPDATE business_automation_rules r
     SET updated_at = now()
   WHERE r.rule_id IN (SELECT rule_id FROM due)
  RETURNING r.*;
END;
$$;
REVOKE EXECUTE ON FUNCTION claim_due_business_automation_rules(INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_due_business_automation_rules(INTEGER) TO service_role;

-- Reserve execution intent before side effects. The unique tenant/key fence makes
-- replay deterministic; an existing started/uncertain execution is returned rather
-- than granting a second worker permission to execute it.
CREATE OR REPLACE FUNCTION reserve_business_automation_execution(p_execution JSONB)
RETURNS SETOF business_automation_executions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  r business_automation_executions;
  now_ts timestamptz := now();
  stale_after interval := interval '15 minutes';
BEGIN
  SELECT * INTO r
    FROM business_automation_executions
   WHERE workspace_id = p_execution->>'workspace_id'
     AND idempotency_key = p_execution->>'idempotency_key'
   FOR UPDATE;

  IF FOUND THEN
    -- A worker may have died after reserving but before finalizing. Reclaim
    -- only stale reservations, and increment the attempt atomically.
    IF r.status = 'started' AND r.started_at < now_ts - stale_after THEN
      UPDATE business_automation_executions
         SET started_at = now_ts, finished_at = now_ts, attempt = r.attempt + 1,
             output = jsonb_build_object('_reservation_acquired', true), error = NULL
       WHERE execution_id = r.execution_id;
      SELECT * INTO r FROM business_automation_executions WHERE execution_id = r.execution_id;
    END IF;
    RETURN NEXT r;
    RETURN;
  END IF;

  INSERT INTO business_automation_executions(
    execution_id, rule_id, workspace_id, action, status, dry_run,
    started_at, finished_at, output, error, idempotency_key, attempt)
  VALUES(
    p_execution->>'execution_id', p_execution->>'rule_id', p_execution->>'workspace_id',
    p_execution->>'action', 'started', COALESCE((p_execution->>'dry_run')::boolean, false),
    COALESCE((p_execution->>'started_at')::timestamptz, now_ts), now_ts,
    jsonb_build_object('_reservation_acquired', true), NULL, p_execution->>'idempotency_key', 1);

  SELECT * INTO r FROM business_automation_executions
   WHERE execution_id = p_execution->>'execution_id';
  RETURN NEXT r;
END;
$$;
REVOKE EXECUTE ON FUNCTION reserve_business_automation_execution(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION reserve_business_automation_execution(JSONB) TO service_role;
