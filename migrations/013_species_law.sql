-- Phase A — Species Law
-- Run in Supabase SQL editor after 001–012.

-- 1. decision_id on automation jobs (organism authorization trail)
ALTER TABLE IF EXISTS automation_jobs
    ADD COLUMN IF NOT EXISTS decision_id TEXT,
    ADD COLUMN IF NOT EXISTS correlation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_automation_jobs_decision
    ON automation_jobs(decision_id);
CREATE INDEX IF NOT EXISTS idx_automation_jobs_correlation
    ON automation_jobs(correlation_id);

-- 2. Revoke dangerous anon grants from 008_hardening
REVOKE ALL ON public.api_keys FROM anon;
REVOKE ALL ON public.audit_logs FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.api_keys TO service_role;
GRANT SELECT, INSERT ON public.audit_logs TO service_role;

-- 3. Enable RLS on tenant tables (service_role still bypasses;
--    this protects any future anon/authenticated client access)
ALTER TABLE IF EXISTS workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS brands ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS ai_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS social_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS api_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS audit_logs ENABLE ROW LEVEL SECURITY;

-- Drop prior policies if re-running
DO $$ BEGIN
  DROP POLICY IF EXISTS workspaces_member_select ON workspaces;
  DROP POLICY IF EXISTS brands_workspace_isolation ON brands;
  DROP POLICY IF EXISTS social_accounts_workspace_isolation ON social_accounts;
  DROP POLICY IF EXISTS api_tokens_workspace_isolation ON api_tokens;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

-- Members can read their workspaces (authenticated role via Supabase Auth)
CREATE POLICY workspaces_member_select ON workspaces
    FOR SELECT
    USING (
        id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
        OR owner_email = auth.jwt()->>'email'
    );

CREATE POLICY brands_workspace_isolation ON brands
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    );

CREATE POLICY social_accounts_workspace_isolation ON social_accounts
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    );

CREATE POLICY api_tokens_workspace_isolation ON api_tokens
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    );

-- 4. Note: backend uses service_role and MUST scope every query by
--    workspace_id from SpeciesAuth. RLS is defense-in-depth for clients.
