-- V65: canonical business-understanding event metadata.
-- The understanding itself remains in the durable event envelope so the event
-- stream is the source of truth and no second mutable AI-state store is needed.
-- This migration reserves explicit event schema versioning for V65 consumers.
UPDATE public.events
SET schema_version = '1.0'
WHERE schema_version IS NULL;

CREATE INDEX IF NOT EXISTS events_type_parent_idx
    ON public.events (event_type, parent_event_id);
