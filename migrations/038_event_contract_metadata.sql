-- Phase 3: canonical event envelope metadata.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS decision_id TEXT,
    ADD COLUMN IF NOT EXISTS brand_id TEXT,
    ADD COLUMN IF NOT EXISTS parent_event_id TEXT,
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT '1.0';

CREATE INDEX IF NOT EXISTS events_decision_idx ON public.events (decision_id);
CREATE INDEX IF NOT EXISTS events_parent_idx ON public.events (parent_event_id);
