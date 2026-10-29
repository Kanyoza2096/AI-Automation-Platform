-- V71.3 Agent Memory Plane
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_memory_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_fingerprint TEXT NOT NULL,
    CONSTRAINT agent_memories_confidence_ck CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT agent_memories_status_ck CHECK (status IN ('active','revoked','expired','superseded'))
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_workspace_agent ON agent_memories(workspace_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_subject ON agent_memories(workspace_id, agent_id, subject);
CREATE INDEX IF NOT EXISTS idx_agent_memories_status ON agent_memories(workspace_id, agent_id, status);
