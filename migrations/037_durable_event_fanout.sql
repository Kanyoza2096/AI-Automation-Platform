-- Phase 3: durable per-consumer event fan-out.
-- A single events.processed flag cannot represent multiple independent consumers.
CREATE TABLE IF NOT EXISTS public.event_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
    consumer_group TEXT NOT NULL,
    event_type TEXT NOT NULL,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at TIMESTAMPTZ,
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    processing_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_id, consumer_group)
);

CREATE INDEX IF NOT EXISTS event_deliveries_claim_idx
    ON public.event_deliveries (consumer_group, created_at)
    WHERE processed = FALSE;

CREATE INDEX IF NOT EXISTS event_deliveries_event_idx
    ON public.event_deliveries (event_id);

ALTER TABLE public.event_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS event_deliveries_workspace_isolation ON public.event_deliveries;
CREATE POLICY event_deliveries_workspace_isolation ON public.event_deliveries
FOR ALL
USING (
    EXISTS (
        SELECT 1
        FROM public.events e
        JOIN public.workspace_members wm ON wm.workspace_id = e.workspace_id
        WHERE e.id = event_deliveries.event_id
          AND wm.user_id = auth.uid()::text
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM public.events e
        JOIN public.workspace_members wm ON wm.workspace_id = e.workspace_id
        WHERE e.id = event_deliveries.event_id
          AND wm.user_id = auth.uid()::text
    )
);
