-- Durable cursor for bounded autonomous knowledge review sweeps.
-- Service-role only; this is scheduler state, not tenant-facing data.
CREATE TABLE IF NOT EXISTS public.knowledge_evidence_review_sweep_state (
    state_key TEXT PRIMARY KEY,
    last_entry_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.knowledge_evidence_review_sweep_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.knowledge_evidence_review_sweep_state FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.knowledge_evidence_review_sweep_state TO service_role;

INSERT INTO public.knowledge_evidence_review_sweep_state (state_key, last_entry_id)
VALUES ('global', NULL)
ON CONFLICT (state_key) DO NOTHING;
