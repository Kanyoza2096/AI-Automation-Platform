
CREATE TABLE feature_flags (
    id TEXT PRIMARY KEY,
    feature TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    workspace_id TEXT NOT NULL DEFAULT 'global',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT DEFAULT 'system'
);

CREATE INDEX idx_feature_flags_workspace ON feature_flags(workspace_id);
