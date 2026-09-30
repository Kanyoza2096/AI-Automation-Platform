-- Durable multi-worker event claiming and acknowledgement.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS processed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS processing_error TEXT,
    ADD COLUMN IF NOT EXISTS source_service TEXT,
    ADD COLUMN IF NOT EXISTS source_instance TEXT;

CREATE INDEX IF NOT EXISTS events_unprocessed_claim_idx
    ON public.events (created_at)
    WHERE processed = FALSE AND claimed_at IS NULL;

-- Keep the legacy shared_event_bus consumer safe too.
ALTER TABLE public.shared_event_bus
    ADD COLUMN IF NOT EXISTS processed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS claimed_by TEXT,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS processing_error TEXT,
    ADD COLUMN IF NOT EXISTS source_service TEXT;

CREATE INDEX IF NOT EXISTS shared_event_bus_unprocessed_claim_idx
    ON public.shared_event_bus (created_at)
    WHERE processed = FALSE AND claimed_at IS NULL;
