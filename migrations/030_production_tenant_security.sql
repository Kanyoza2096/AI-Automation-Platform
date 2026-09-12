-- Phase 6: production tenant/security hardening.
-- Backend uses Supabase service_role, so application checks remain mandatory.
-- These policies are defense-in-depth for direct anon/authenticated access.

-- Sensitive tables must never be writable by a browser/client session.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'api_tokens', 'audit_log', 'decision_outcomes', 'social_accounts'
  ] LOOP
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=t) THEN
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_client_deny', t);
      -- No permissive client policy is created. service_role bypasses RLS.
    END IF;
  END LOOP;
END $$;

-- Workspace-scoped read-only data may be read by authenticated members.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='brands') THEN
    ALTER TABLE brands ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS brands_workspace_select ON brands;
    CREATE POLICY brands_workspace_select ON brands FOR SELECT
      USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text AND accepted_at IS NOT NULL));
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ai_profiles') THEN
    ALTER TABLE ai_profiles ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS ai_profiles_workspace_select ON ai_profiles;
    CREATE POLICY ai_profiles_workspace_select ON ai_profiles FOR SELECT
      USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text AND accepted_at IS NOT NULL));
  END IF;
END $$;

-- Workspace membership is self-readable only from direct client sessions.
-- Administrative membership management remains backend/service-role controlled.
ALTER TABLE IF EXISTS workspace_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_members_self_select ON workspace_members;
CREATE POLICY workspace_members_self_select ON workspace_members FOR SELECT
  USING (user_id = auth.uid()::text AND accepted_at IS NOT NULL);

-- Verified outcomes are evidence, not client-controlled training labels.
-- There is intentionally NO INSERT/UPDATE/DELETE policy for anon/authenticated
-- clients. The service_role backend writes outcomes after verification.
ALTER TABLE IF EXISTS decision_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS decision_outcomes_workspace_select ON decision_outcomes;
CREATE POLICY decision_outcomes_workspace_select ON decision_outcomes FOR SELECT
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text AND accepted_at IS NOT NULL));
DROP POLICY IF EXISTS decision_outcomes_workspace_insert ON decision_outcomes;

-- Explicitly prevent mutation of evidence by normal database roles.
DROP POLICY IF EXISTS decision_outcomes_workspace_update ON decision_outcomes;
DROP POLICY IF EXISTS decision_outcomes_workspace_delete ON decision_outcomes;

CREATE INDEX IF NOT EXISTS idx_workspace_members_user_accepted
  ON workspace_members(user_id, accepted_at, workspace_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_workspace
  ON social_accounts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_brands_workspace
  ON brands(workspace_id);
