-- V71.11 — Workforce observability and operator control.
-- This is a control/read plane. It does not execute providers or bypass governance.
CREATE TABLE IF NOT EXISTS agent_workforce_events (
    event_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_autonomous_runs(run_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_workforce_events_run_time
    ON agent_workforce_events(workspace_id, run_id, occurred_at);

CREATE OR REPLACE FUNCTION pause_agent_autonomous_run(p_workspace_id TEXT, p_run_id TEXT, p_actor_id TEXT, p_reason TEXT)
RETURNS SETOF agent_autonomous_runs LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN QUERY UPDATE agent_autonomous_runs
  SET status='paused', reconciliation_required=(status='running'),
      result_summary_json=COALESCE(result_summary_json,'{}'::jsonb) || jsonb_build_object('operator_control','pause','actor_id',p_actor_id,'reason',p_reason,'controlled_at',NOW()),
      lease_token=NULL, lease_owner=NULL, lease_until=NULL, updated_at=NOW()
  WHERE workspace_id=p_workspace_id AND run_id=p_run_id
    AND status IN ('ready','running','proposed','blocked')
  RETURNING *;
END; $$;

CREATE OR REPLACE FUNCTION fence_agent_autonomous_run(p_workspace_id TEXT, p_run_id TEXT, p_actor_id TEXT, p_reason TEXT)
RETURNS SETOF agent_autonomous_runs LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN QUERY UPDATE agent_autonomous_runs
  SET status='paused', reconciliation_required=TRUE,
      result_summary_json=COALESCE(result_summary_json,'{}'::jsonb) || jsonb_build_object('operator_control','fence','actor_id',p_actor_id,'reason',p_reason,'fenced_at',NOW()),
      lease_token=NULL, lease_owner=NULL, lease_until=NULL, updated_at=NOW()
  WHERE workspace_id=p_workspace_id AND run_id=p_run_id AND status NOT IN ('completed','failed','cancelled')
  RETURNING *;
END; $$;

CREATE OR REPLACE FUNCTION resume_agent_autonomous_run(p_workspace_id TEXT, p_run_id TEXT, p_actor_id TEXT, p_reason TEXT)
RETURNS SETOF agent_autonomous_runs LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN QUERY UPDATE agent_autonomous_runs
  SET status='ready',
      result_summary_json=COALESCE(result_summary_json,'{}'::jsonb) || jsonb_build_object('resumed_by',p_actor_id,'resume_reason',p_reason,'resumed_at',NOW()),
      updated_at=NOW()
  WHERE workspace_id=p_workspace_id AND run_id=p_run_id AND status='paused' AND reconciliation_required=FALSE;
END; $$;

CREATE OR REPLACE FUNCTION cancel_agent_autonomous_run(p_workspace_id TEXT, p_run_id TEXT, p_actor_id TEXT, p_reason TEXT)
RETURNS SETOF agent_autonomous_runs LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN QUERY UPDATE agent_autonomous_runs
  SET status='cancelled',
      result_summary_json=COALESCE(result_summary_json,'{}'::jsonb) || jsonb_build_object('cancelled_by',p_actor_id,'reason',p_reason,'cancelled_at',NOW()),
      lease_token=NULL, lease_owner=NULL, lease_until=NULL, updated_at=NOW()
  WHERE workspace_id=p_workspace_id AND run_id=p_run_id AND status NOT IN ('completed','failed','cancelled');
END; $$;

REVOKE ALL ON FUNCTION pause_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION fence_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION resume_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION cancel_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pause_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION fence_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION resume_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION cancel_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT) TO service_role;
