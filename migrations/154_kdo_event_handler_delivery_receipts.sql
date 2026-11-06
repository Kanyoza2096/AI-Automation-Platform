-- Durable per-handler receipts for SharedEventBus at-least-once delivery.
-- A successful handler receipt prevents that handler from being replayed when
-- another handler in the same consumer group fails and causes a retry.

CREATE TABLE public.event_handler_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id text NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
    consumer_group text NOT NULL,
    handler_key text NOT NULL CHECK (length(handler_key) BETWEEN 1 AND 512),
    processed boolean NOT NULL DEFAULT false,
    processed_at timestamptz,
    claimed_by text,
    claimed_at timestamptz,
    processing_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT event_handler_deliveries_event_group_handler_key
        UNIQUE (event_id, consumer_group, handler_key)
);

CREATE INDEX event_handler_deliveries_claim_idx
    ON public.event_handler_deliveries (consumer_group, created_at)
    WHERE processed = false;

CREATE INDEX event_handler_deliveries_event_idx
    ON public.event_handler_deliveries (event_id);

ALTER TABLE public.event_handler_deliveries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.event_handler_deliveries FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.event_handler_deliveries TO service_role;

COMMENT ON TABLE public.event_handler_deliveries IS
    'Durable per-handler receipts for at-least-once shared event delivery; a handler is skipped after its successful receipt is persisted.';
