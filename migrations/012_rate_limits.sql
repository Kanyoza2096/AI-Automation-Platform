CREATE TABLE IF NOT EXISTS rate_limits (
    workspace_id TEXT PRIMARY KEY,
    api_per_user INTEGER DEFAULT 30,
    ai_generate INTEGER DEFAULT 10,
    publish_post INTEGER DEFAULT 5,
    auth_attempts INTEGER DEFAULT 10,
    webhook_calls INTEGER DEFAULT 60,
    file_upload INTEGER DEFAULT 10,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
