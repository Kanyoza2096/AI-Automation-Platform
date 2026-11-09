-- U2: store producer-signed provenance separately from event payload.
-- Nullable by design: legacy and unsigned publishers remain untrusted.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS provenance_envelope JSONB;

ALTER TABLE public.events
    DROP CONSTRAINT IF EXISTS events_provenance_envelope_object;

ALTER TABLE public.events
    ADD CONSTRAINT events_provenance_envelope_object
    CHECK (
        provenance_envelope IS NULL
        OR jsonb_typeof(provenance_envelope) = 'object'
    );

COMMENT ON COLUMN public.events.provenance_envelope IS
    'Optional producer-signed provenance envelope. Consumers must independently verify it and never trust serialized verification flags.';
