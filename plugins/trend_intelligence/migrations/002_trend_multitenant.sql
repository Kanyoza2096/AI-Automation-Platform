-- Trend Intelligence multi-tenant hardening (run in Supabase SQL editor)
-- Safe / idempotent where possible.

-- 1) workspace_id columns (nullable for legacy rows)
ALTER TABLE IF EXISTS trend_sources
  ADD COLUMN IF NOT EXISTS workspace_id TEXT;

ALTER TABLE IF EXISTS trend_posts
  ADD COLUMN IF NOT EXISTS workspace_id TEXT;

ALTER TABLE IF EXISTS trend_brand_rules
  ADD COLUMN IF NOT EXISTS workspace_id TEXT;

ALTER TABLE IF EXISTS trend_decisions
  ADD COLUMN IF NOT EXISTS workspace_id TEXT;

ALTER TABLE IF EXISTS trend_page_posts
  ADD COLUMN IF NOT EXISTS workspace_id TEXT;

-- 2) Durable URL uniqueness
CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_url_unique
  ON trend_posts (url);

-- Prefer tenant-scoped uniqueness when workspace_id is populated
CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_workspace_url_unique
  ON trend_posts (workspace_id, url)
  WHERE workspace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS trend_posts_workspace_fetched_idx
  ON trend_posts (workspace_id, fetched_at DESC);

CREATE INDEX IF NOT EXISTS trend_brand_rules_workspace_idx
  ON trend_brand_rules (workspace_id)
  WHERE workspace_id IS NOT NULL;

-- 3) Publish jobs table
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

CREATE INDEX IF NOT EXISTS trend_publish_jobs_workspace_idx
  ON trend_publish_jobs (workspace_id, requested_at DESC);

-- 4) RLS (enable + policies). Uses request header workspace via JWT claim
--    or service role (bypasses RLS). Adjust claim path to your auth model.

ALTER TABLE IF EXISTS trend_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_brand_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_publish_jobs ENABLE ROW LEVEL SECURITY;

-- Helper: workspace from JWT (custom claim) — null means service/legacy
-- Policy pattern: allow if row.workspace_id is null (legacy) OR matches claim

DROP POLICY IF EXISTS trend_posts_tenant_isolation ON trend_posts;
CREATE POLICY trend_posts_tenant_isolation ON trend_posts
  FOR ALL
  USING (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  )
  WITH CHECK (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS trend_sources_tenant_isolation ON trend_sources;
CREATE POLICY trend_sources_tenant_isolation ON trend_sources
  FOR ALL
  USING (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  )
  WITH CHECK (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS trend_brand_rules_tenant_isolation ON trend_brand_rules;
CREATE POLICY trend_brand_rules_tenant_isolation ON trend_brand_rules
  FOR ALL
  USING (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  )
  WITH CHECK (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS trend_decisions_tenant_isolation ON trend_decisions;
CREATE POLICY trend_decisions_tenant_isolation ON trend_decisions
  FOR ALL
  USING (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  )
  WITH CHECK (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS trend_publish_jobs_tenant_isolation ON trend_publish_jobs;
CREATE POLICY trend_publish_jobs_tenant_isolation ON trend_publish_jobs
  FOR ALL
  USING (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  )
  WITH CHECK (
    workspace_id IS NULL
    OR workspace_id = coalesce(auth.jwt() ->> 'workspace_id', auth.jwt() ->> 'tenant_id', '')
    OR auth.role() = 'service_role'
  );

-- Note: server uses service_role key → RLS bypassed for workers (expected).
-- Client-facing API keys should use anon/authenticated with workspace claim.
