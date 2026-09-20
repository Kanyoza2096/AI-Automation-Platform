-- Phase 91: RBAC/RLS defense-in-depth.
-- The application and KCPG remain authoritative. These policies prevent a
-- browser/Supabase authenticated session from bypassing those controls by
-- writing directly to privileged tenant tables. service_role is unaffected.

-- Legacy Phase 013 policies granted FOR ALL to every workspace member.
-- Remove those broad policies before adding read-only client policies.
DO $$
BEGIN
  IF to_regclass('public.brands') IS NOT NULL THEN
    DROP POLICY IF EXISTS brands_workspace_isolation ON public.brands;
    DROP POLICY IF EXISTS brands_workspace_select ON public.brands;
    CREATE POLICY brands_workspace_select ON public.brands FOR SELECT
      USING (workspace_id IN (
        SELECT workspace_id FROM public.workspace_members
        WHERE user_id = auth.uid()::text AND accepted_at IS NOT NULL
      ));
  END IF;

  IF to_regclass('public.social_accounts') IS NOT NULL THEN
    DROP POLICY IF EXISTS social_accounts_workspace_isolation ON public.social_accounts;
    DROP POLICY IF EXISTS social_accounts_workspace_select ON public.social_accounts;
    -- No client write policy: social credentials/bindings are backend controlled.
    CREATE POLICY social_accounts_workspace_select ON public.social_accounts FOR SELECT
      USING (workspace_id IN (
        SELECT workspace_id FROM public.workspace_members
        WHERE user_id = auth.uid()::text AND accepted_at IS NOT NULL
      ));
  END IF;

  IF to_regclass('public.api_tokens') IS NOT NULL THEN
    DROP POLICY IF EXISTS api_tokens_workspace_isolation ON public.api_tokens;
    DROP POLICY IF EXISTS api_tokens_workspace_select ON public.api_tokens;
    -- No client read/write policy: token material is backend controlled.
  END IF;
END $$;

-- Affiliate workload is also governed by the backend Action Kernel/API.
-- Remove member-wide write policies created in Phase 048.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'affiliate_products', 'affiliate_links', 'affiliate_clicks',
    'affiliate_conversions', 'affiliate_revenue', 'affiliate_campaigns'
  ] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('DROP POLICY IF EXISTS %I_workspace_write ON public.%I', t, t);
    END IF;
  END LOOP;
END $$;

-- Business automation mutations are also backend-governed. Keep workspace
-- visibility for authenticated members but remove direct client writes.
DO $$
BEGIN
  IF to_regclass('public.business_automation_rules') IS NOT NULL THEN
    DROP POLICY IF EXISTS business_automation_rules_workspace_insert ON public.business_automation_rules;
    DROP POLICY IF EXISTS business_automation_rules_workspace_update ON public.business_automation_rules;
    DROP POLICY IF EXISTS business_automation_rules_workspace_delete ON public.business_automation_rules;
  END IF;
END $$;
