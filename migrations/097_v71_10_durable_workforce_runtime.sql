-- V71.10 — Durable autonomous workforce runtime.
-- The run row remains the business-run identity; this adds worker lease state
-- and immutable runtime evidence without creating a second execution plane.
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS attempt BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS lease_token TEXT;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE agent_autonomous_runs
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_claimable
ON agent_autonomous_runs(workspace_id, status, lease_until);

-- Claim only READY runs. A RUNNING run with an expired lease is never reclaimed:
-- it must first be fenced as uncertain/reconciliation-required because the old
-- worker may have crossed the external side-effect boundary.
CREATE OR REPLACE FUNCTION claim_agent_autonomous_run(
    p_workspace_id TEXT,
    p_run_id TEXT,
    p_worker_id TEXT,
    p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF agent_autonomous_runs
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_token TEXT := encode(gen_random_bytes(16), 'hex');
BEGIN
    IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'invalid lease seconds';
    END IF;
    RETURN QUERY
    UPDATE agent_autonomous_runs
       SET status = 'running',
           attempt = attempt + 1,
           lease_token = v_token,
           lease_owner = p_worker_id,
           lease_until = NOW() + make_interval(secs => p_lease_seconds),
           last_heartbeat_at = NOW(),
           updated_at = NOW()
     WHERE workspace_id = p_workspace_id
       AND run_id = p_run_id
       AND status = 'ready'
       AND (lease_until IS NULL OR lease_until < NOW())
    RETURNING agent_autonomous_runs.*;
END;
$$;

CREATE OR REPLACE FUNCTION renew_agent_autonomous_run_lease(
    p_workspace_id TEXT,
    p_run_id TEXT,
    p_worker_id TEXT,
    p_lease_token TEXT,
    p_lease_seconds INTEGER DEFAULT 300
) RETURNS TABLE(lease_until TIMESTAMPTZ, attempt BIGINT)
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'invalid lease seconds';
    END IF;
    RETURN QUERY
    UPDATE agent_autonomous_runs
       SET lease_until = NOW() + make_interval(secs => p_lease_seconds),
           last_heartbeat_at = NOW(), updated_at = NOW()
     WHERE workspace_id = p_workspace_id AND run_id = p_run_id
       AND status = 'running' AND lease_owner = p_worker_id
       AND lease_token = p_lease_token AND lease_until > NOW()
    RETURNING agent_autonomous_runs.lease_until, agent_autonomous_runs.attempt;
END;
$$;

CREATE OR REPLACE FUNCTION finalize_agent_autonomous_run(
    p_workspace_id TEXT,
    p_run_id TEXT,
    p_worker_id TEXT,
    p_lease_token TEXT,
    p_status TEXT,
    p_disposition TEXT DEFAULT NULL,
    p_result_summary JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF agent_autonomous_runs
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF p_status NOT IN ('completed','failed','blocked','paused') THEN
        RAISE EXCEPTION 'invalid terminal runtime status';
    END IF;
    RETURN QUERY
    UPDATE agent_autonomous_runs
       SET status = p_status,
           disposition = COALESCE(p_disposition, disposition),
           result_summary_json = COALESCE(p_result_summary, '{}'::jsonb),
           reconciliation_required = (p_status = 'paused'),
           lease_token = NULL, lease_owner = NULL, lease_until = NULL,
           updated_at = NOW()
     WHERE workspace_id = p_workspace_id AND run_id = p_run_id
       AND status = 'running' AND lease_owner = p_worker_id
       AND lease_token = p_lease_token AND lease_until > NOW()
    RETURNING agent_autonomous_runs.*;
END;
$$;

CREATE OR REPLACE FUNCTION mark_agent_autonomous_run_uncertain(
    p_workspace_id TEXT,
    p_run_id TEXT
) RETURNS SETOF agent_autonomous_runs
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    RETURN QUERY
    UPDATE agent_autonomous_runs
       SET status = 'paused',
           reconciliation_required = TRUE,
           result_summary_json = COALESCE(result_summary_json, '{}'::jsonb) ||
             jsonb_build_object('reconciliation_required', TRUE,
                                'reason', 'lease_expired', 'marked_at', NOW()),
           lease_token = NULL, lease_owner = NULL, lease_until = NULL,
           updated_at = NOW()
     WHERE workspace_id = p_workspace_id AND run_id = p_run_id
       AND status = 'running'
       AND lease_until IS NOT NULL AND lease_until < NOW()
    RETURNING agent_autonomous_runs.*;
END;
$$;

REVOKE ALL ON FUNCTION claim_agent_autonomous_run(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION renew_agent_autonomous_run_lease(TEXT,TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION finalize_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION mark_agent_autonomous_run_uncertain(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_agent_autonomous_run(TEXT,TEXT,TEXT,INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION renew_agent_autonomous_run_lease(TEXT,TEXT,TEXT,TEXT,INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION finalize_agent_autonomous_run(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION mark_agent_autonomous_run_uncertain(TEXT,TEXT) TO service_role;
