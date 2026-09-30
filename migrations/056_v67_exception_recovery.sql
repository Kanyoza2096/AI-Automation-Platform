-- V67: durable exception evidence and bounded recovery planning.
-- This table is advisory/governance state only. It is NOT an execution queue.
CREATE TABLE IF NOT EXISTS public.autonomous_exceptions (
  exception_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  execution_id TEXT NOT NULL,
  intent_id TEXT,
  action_type TEXT NOT NULL,
  classification TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  observed_status TEXT NOT NULL,
  error TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  external_state_uncertain BOOLEAN NOT NULL DEFAULT false,
  partial_execution BOOLEAN NOT NULL DEFAULT false,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','recovery_planned','reconciling','recovered','quarantined','blocked','review_required')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_autonomous_exception_workspace_fingerprint
  ON public.autonomous_exceptions(workspace_id, fingerprint, execution_id);
CREATE INDEX IF NOT EXISTS idx_autonomous_exceptions_workspace_state
  ON public.autonomous_exceptions(workspace_id, state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_exceptions_execution
  ON public.autonomous_exceptions(execution_id);

CREATE TABLE IF NOT EXISTS public.autonomous_recovery_plans (
  plan_id TEXT PRIMARY KEY,
  exception_id TEXT NOT NULL REFERENCES public.autonomous_exceptions(exception_id),
  workspace_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  disposition TEXT NOT NULL CHECK (disposition IN ('auto_recover','reconcile_first','review_required','blocked','quarantined')),
  recovery_action TEXT NOT NULL CHECK (recovery_action IN ('retry_existing_intent','reconcile_external_state','refresh_dependency_auth','none')),
  reason TEXT NOT NULL,
  max_attempts INTEGER NOT NULL DEFAULT 0,
  next_delay_seconds INTEGER NOT NULL DEFAULT 0,
  requires_human_approval BOOLEAN NOT NULL DEFAULT false,
  requires_reconciliation BOOLEAN NOT NULL DEFAULT false,
  authority TEXT NOT NULL DEFAULT 'none' CHECK (authority = 'none'),
  steps JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autonomous_recovery_workspace
  ON public.autonomous_recovery_plans(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_recovery_exception
  ON public.autonomous_recovery_plans(exception_id, created_at DESC);

ALTER TABLE public.autonomous_exceptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.autonomous_recovery_plans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS autonomous_exceptions_workspace_select ON public.autonomous_exceptions;
CREATE POLICY autonomous_exceptions_workspace_select ON public.autonomous_exceptions FOR SELECT
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS autonomous_recovery_plans_workspace_select ON public.autonomous_recovery_plans;
CREATE POLICY autonomous_recovery_plans_workspace_select ON public.autonomous_recovery_plans FOR SELECT
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

-- Inserts/updates are intentionally service-role only. Recovery planning is a
-- control-plane concern and must not become a client-side authority surface.

-- Recovery release is intentionally narrow: only an already-failed action intent
-- may be returned to pending. Reconciliation-required/processing/succeeded rows
-- cannot be reset by this function.
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS recovery_plan_id TEXT;
CREATE INDEX IF NOT EXISTS idx_action_outbox_recovery_plan
  ON public.action_outbox(recovery_plan_id)
  WHERE recovery_plan_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.release_action_outbox_for_recovery(
  p_intent_id TEXT, p_workspace_id TEXT, p_plan_id TEXT, p_delay_seconds INTEGER DEFAULT 0
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_delay_seconds < 0 OR p_delay_seconds > 3600 THEN
    RAISE EXCEPTION 'invalid recovery delay';
  END IF;
  UPDATE public.action_outbox
     SET status = 'pending',
         available_at = now() + make_interval(secs => p_delay_seconds),
         recovery_plan_id = p_plan_id,
         updated_at = now(),
         lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
   WHERE intent_id = p_intent_id
     AND workspace_id = p_workspace_id
     AND status = 'failed';
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_action_outbox_for_recovery(TEXT,TEXT,TEXT,INTEGER) TO service_role;
