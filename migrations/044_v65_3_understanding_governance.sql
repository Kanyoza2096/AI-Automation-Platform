-- V65.3: evidence/confidence governance is embedded in immutable understanding
-- event payloads. No AI authority or side-effect capability is introduced.
CREATE INDEX IF NOT EXISTS events_business_understanding_workspace_created_idx
    ON public.events (workspace_id, event_type, created_at DESC)
    WHERE event_type = 'business.understanding.completed';
