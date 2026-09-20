-- V71.12 certification evidence record. Certification never grants authority.
CREATE TABLE IF NOT EXISTS agent_workforce_certification (
    certification_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PASS','FAIL','NOT_PROVEN')),
    findings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    certified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_workforce_certification_workspace_time
    ON agent_workforce_certification(workspace_id, certified_at DESC);
