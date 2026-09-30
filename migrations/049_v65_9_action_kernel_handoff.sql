-- V65.9: durable decision -> Action Kernel admission ledger.
-- This is an admission/audit record, not an execution queue. The Action Kernel
-- must independently validate and enqueue an ActionIntent before side effects.
CREATE TABLE IF NOT EXISTS public.business_decision_action_handoffs (
  handoff_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  understanding_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  action_type TEXT NOT NULL,
  risk_level TEXT,
  policy_version TEXT NOT NULL,
  policy_disposition TEXT NOT NULL CHECK (policy_disposition IN ('ALLOW','APPROVAL_REQUIRED','REVIEW','BLOCK')),
  state TEXT NOT NULL CHECK (state IN ('ready','approval_required','blocked')),
  approval_required BOOLEAN NOT NULL DEFAULT false,
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  reason TEXT NOT NULL,
  authority TEXT NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  fingerprint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_decision_action_handoff_idempotency
  ON public.business_decision_action_handoffs(workspace_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_business_decision_action_handoff_eval
  ON public.business_decision_action_handoffs(workspace_id, evaluation_id);
CREATE INDEX IF NOT EXISTS idx_business_decision_action_handoff_state
  ON public.business_decision_action_handoffs(workspace_id, state, created_at DESC);

ALTER TABLE public.business_decision_action_handoffs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_decision_action_handoff_select ON public.business_decision_action_handoffs;
CREATE POLICY business_decision_action_handoff_select ON public.business_decision_action_handoffs FOR SELECT
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS business_decision_action_handoff_insert ON public.business_decision_action_handoffs;
CREATE POLICY business_decision_action_handoff_insert ON public.business_decision_action_handoffs FOR INSERT
  WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
