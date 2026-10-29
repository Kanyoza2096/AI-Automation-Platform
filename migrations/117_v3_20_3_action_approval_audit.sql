-- IPC-03: approvals are durable governance evidence, not an unqualified boolean.
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS approved_role TEXT;
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS approval_source TEXT;

CREATE INDEX IF NOT EXISTS idx_action_outbox_approval_audit
  ON public.action_outbox(workspace_id, approved_at DESC)
  WHERE approved = true;
