-- V71.17 — durable handoff between a workforce run and the existing execution plane.
CREATE TABLE IF NOT EXISTS agent_execution_handoffs (
    handoff_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_autonomous_runs(run_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','dispatching','accepted','awaiting_execution','completed','failed','uncertain','blocked')),
    execution_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    intent_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    handoff_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(workspace_id, run_id),
    UNIQUE(workspace_id, correlation_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_execution_handoffs_workspace_status
ON agent_execution_handoffs(workspace_id, status, updated_at);

CREATE OR REPLACE FUNCTION claim_agent_execution_handoff(
    p_workspace_id TEXT,
    p_handoff_id TEXT
) RETURNS SETOF agent_execution_handoffs
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    RETURN QUERY
    UPDATE agent_execution_handoffs
       SET status = 'dispatching', updated_at = NOW()
     WHERE workspace_id = p_workspace_id
       AND handoff_id = p_handoff_id
       AND status = 'pending'
    RETURNING agent_execution_handoffs.*;
END;
$$;
REVOKE ALL ON FUNCTION claim_agent_execution_handoff(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_agent_execution_handoff(TEXT,TEXT) TO service_role;
