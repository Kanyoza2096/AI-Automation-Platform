-- V65.2: canonical KDO-owned business signals.
-- Signal projections live in durable event payloads; no mutable AI state table.
CREATE INDEX IF NOT EXISTS events_type_workspace_created_idx
    ON public.events (event_type, workspace_id, created_at DESC);
