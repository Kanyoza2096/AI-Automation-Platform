-- Optional durable uniqueness for trend posts (run in Supabase SQL editor)

CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_url_unique
  ON trend_posts (url);

-- Multi-tenant form when workspace_id column exists:
-- CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_workspace_url_unique
--   ON trend_posts (workspace_id, url);

CREATE TABLE IF NOT EXISTS trend_publish_jobs (
  job_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  brand_id TEXT,
  trend_id TEXT,
  topic TEXT,
  content TEXT,
  url TEXT,
  platform TEXT,
  decision TEXT,
  relevance DOUBLE PRECISION,
  correlation_id TEXT,
  media_url TEXT,
  status TEXT,
  requested_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
