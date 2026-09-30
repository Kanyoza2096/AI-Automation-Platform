-- Phase 55: asynchronous action-outbox worker approval state.
ALTER TABLE public.action_outbox ADD COLUMN IF NOT EXISTS approved BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.action_outbox DROP CONSTRAINT IF EXISTS action_outbox_status_check;
ALTER TABLE public.action_outbox ADD CONSTRAINT action_outbox_status_check CHECK (status IN ('pending','processing','succeeded','failed','reconciliation_required','cancelled','approval_required'));
CREATE INDEX IF NOT EXISTS idx_action_outbox_approval_due ON public.action_outbox(status, approved, available_at, created_at) WHERE status IN ('pending','approval_required');
