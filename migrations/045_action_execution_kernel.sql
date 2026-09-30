-- Phase 54: generic governed action intent/outbox.
-- Publishing keeps its compatibility table/pipeline; business actions use this
-- table as the durable intent boundary before any side effect.
CREATE TABLE IF NOT EXISTS public.action_outbox (
  intent_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  risk_level TEXT NOT NULL DEFAULT 'low' CHECK (risk_level IN ('low','medium','high','critical')),
  decision_id TEXT,
  correlation_id TEXT,
  idempotency_key TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','succeeded','failed','reconciliation_required','cancelled')),
  execution_id TEXT,
  provider_object_id TEXT,
  output JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_outbox_workspace_idempotency
  ON public.action_outbox(workspace_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_action_outbox_due
  ON public.action_outbox(status, available_at, created_at)
  WHERE status IN ('pending','processing');
CREATE INDEX IF NOT EXISTS idx_action_outbox_workspace
  ON public.action_outbox(workspace_id, created_at DESC);

ALTER TABLE public.action_outbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS action_outbox_workspace_select ON public.action_outbox;
CREATE POLICY action_outbox_workspace_select ON public.action_outbox FOR SELECT
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS action_outbox_workspace_insert ON public.action_outbox;
CREATE POLICY action_outbox_workspace_insert ON public.action_outbox FOR INSERT
  WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS action_outbox_workspace_update ON public.action_outbox;
CREATE POLICY action_outbox_workspace_update ON public.action_outbox FOR UPDATE
  USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text))
  WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

-- Worker claim is service-role only. A live processing row is never silently
-- treated as safe to duplicate; recovery must reconcile through the execution
-- ledger/provider contract before re-running an ambiguous side effect.
CREATE OR REPLACE FUNCTION public.claim_action_outbox(p_limit INTEGER DEFAULT 25, p_lease_seconds INTEGER DEFAULT 120)
RETURNS SETOF public.action_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_worker TEXT := 'action:' || gen_random_uuid()::text;
BEGIN
  RETURN QUERY
  WITH candidates AS (
    SELECT intent_id FROM public.action_outbox
    WHERE (status = 'pending' AND available_at <= now())
       OR (status = 'processing' AND updated_at < now() - make_interval(secs => GREATEST(p_lease_seconds, 1)))
    ORDER BY available_at, created_at
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
    FOR UPDATE SKIP LOCKED
  )
  UPDATE public.action_outbox a
     SET status = 'processing', attempts = attempts + 1, updated_at = now()
   WHERE a.intent_id IN (SELECT intent_id FROM candidates)
  RETURNING a.*;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_action_outbox(INTEGER, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_action_outbox(INTEGER, INTEGER) TO service_role;
