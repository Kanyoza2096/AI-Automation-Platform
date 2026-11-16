-- Additive, idempotent columns required by PublishStep's post_history audit record.
-- Safe for existing rows: metadata defaults to an empty JSON object and decision_id is nullable.
ALTER TABLE public.post_history
    ADD COLUMN IF NOT EXISTS decision_id TEXT;

ALTER TABLE public.post_history
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS post_history_decision_id_idx
    ON public.post_history (decision_id)
    WHERE decision_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS post_history_correlation_id_idx
    ON public.post_history ((metadata ->> 'correlation_id'))
    WHERE metadata ? 'correlation_id';
