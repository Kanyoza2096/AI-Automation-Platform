-- C11.6 — Business automation read-path RLS hardening.
-- Authenticated workspace members may read only rules for accepted memberships.
-- Mutations remain backend-governed; service_role is used by workers/backend.
DO $$
BEGIN
  IF to_regclass('public.business_automation_rules') IS NOT NULL THEN
    ALTER TABLE public.business_automation_rules ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS business_automation_rules_workspace_select ON public.business_automation_rules;
    CREATE POLICY business_automation_rules_workspace_select
      ON public.business_automation_rules
      FOR SELECT
      USING (
        workspace_id IN (
          SELECT wm.workspace_id
          FROM public.workspace_members wm
          WHERE wm.user_id = auth.uid()::text
            AND wm.accepted_at IS NOT NULL
        )
      );
  END IF;
END $$;
