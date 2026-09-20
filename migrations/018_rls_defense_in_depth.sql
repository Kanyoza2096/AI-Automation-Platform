-- Migration 018: RLS defense-in-depth for core autonomy tables
-- Architecture report §22–23 — database is final authority for tenant isolation.
-- Backend still uses service_role and MUST scope by SpeciesAuth workspace_id.
-- These policies protect anon/authenticated client access.

-- Enable RLS (idempotent)
ALTER TABLE IF EXISTS events ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS automation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS publish_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS director_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS director_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trend_publish_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS knowledge_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS monitor_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS monitor_checks ENABLE ROW LEVEL SECURITY;

-- Helper: membership predicate (user_id as text to match existing schema)
-- Policies use workspace_members.user_id = auth.uid()::text

DO $$
BEGIN
  -- events
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'events') THEN
    DROP POLICY IF EXISTS events_workspace_isolation ON events;
    BEGIN
      CREATE POLICY events_workspace_isolation ON events
        FOR ALL
        USING (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        )
        WITH CHECK (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        );
    EXCEPTION WHEN undefined_column THEN
      RAISE NOTICE 'events.workspace_id missing — skip policy';
    END;
  END IF;

  -- automation_jobs
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'automation_jobs') THEN
    DROP POLICY IF EXISTS automation_jobs_workspace_isolation ON automation_jobs;
    BEGIN
      CREATE POLICY automation_jobs_workspace_isolation ON automation_jobs
        FOR ALL
        USING (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        )
        WITH CHECK (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        );
    EXCEPTION WHEN undefined_column THEN
      RAISE NOTICE 'automation_jobs policy skipped';
    END;
  END IF;

  -- trend_decisions
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'trend_decisions') THEN
    DROP POLICY IF EXISTS trend_decisions_workspace_isolation ON trend_decisions;
    BEGIN
      CREATE POLICY trend_decisions_workspace_isolation ON trend_decisions
        FOR ALL
        USING (
          workspace_id IS NULL
          OR workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        )
        WITH CHECK (
          workspace_id IS NULL
          OR workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        );
    EXCEPTION WHEN undefined_column THEN
      RAISE NOTICE 'trend_decisions policy skipped';
    END;
  END IF;

  -- publish_outbox
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'publish_outbox') THEN
    DROP POLICY IF EXISTS publish_outbox_workspace_isolation ON publish_outbox;
    BEGIN
      CREATE POLICY publish_outbox_workspace_isolation ON publish_outbox
        FOR ALL
        USING (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        )
        WITH CHECK (
          workspace_id IN (
            SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text
          )
        );
    EXCEPTION WHEN undefined_column THEN
      RAISE NOTICE 'publish_outbox policy skipped';
    END;
  END IF;
END $$;

-- NOTE: service_role bypasses RLS. Application code remains responsible for
-- scoping every query by trusted WorkspaceContext from SpeciesAuth.
