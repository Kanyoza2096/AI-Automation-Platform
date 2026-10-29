-- V1.4 Business Understanding -> Playbook -> Provisioning.
CREATE TABLE IF NOT EXISTS business_onboarding_plans (
  plan_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  business_type TEXT NOT NULL,
  profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  plan JSONB NOT NULL DEFAULT '{}'::jsonb,
  provisioned JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'provisioning' CHECK (status IN ('provisioning','needs_connectors','ready','active','blocked','failed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_business_onboarding_workspace ON business_onboarding_plans(workspace_id, created_at DESC);
ALTER TABLE business_onboarding_plans ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_onboarding_member_select ON business_onboarding_plans;
CREATE POLICY business_onboarding_member_select ON business_onboarding_plans FOR SELECT USING (public.is_workspace_member(workspace_id));
