-- U1: durable retry accounting and dead-letter recovery for shared event deliveries.
ALTER TABLE public.event_deliveries
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS event_deliveries_retry_idx
    ON public.event_deliveries (consumer_group, next_attempt_at, created_at)
    WHERE processed = FALSE AND dead_lettered_at IS NULL;

CREATE OR REPLACE FUNCTION public.complete_shared_event_delivery(
    p_delivery_id UUID,
    p_consumer_group TEXT,
    p_claim_token TEXT,
    p_success BOOLEAN,
    p_error TEXT DEFAULT NULL,
    p_max_attempts INTEGER DEFAULT 5
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE public.event_deliveries
    SET
        processed = p_success,
        processed_at = CASE WHEN p_success THEN NOW() ELSE NULL END,
        processing_error = CASE
            WHEN p_success THEN NULL
            ELSE LEFT(COALESCE(p_error, 'handler failed'), 1000)
        END,
        attempt_count = CASE WHEN p_success THEN attempt_count ELSE attempt_count + 1 END,
        dead_lettered_at = CASE
            WHEN NOT p_success AND attempt_count + 1 >= GREATEST(p_max_attempts, 1)
                THEN NOW()
            ELSE NULL
        END,
        next_attempt_at = CASE
            WHEN p_success OR attempt_count + 1 >= GREATEST(p_max_attempts, 1)
                THEN NOW()
            ELSE NOW() + make_interval(
                secs => LEAST(300, 15 * power(2, LEAST(attempt_count, 4))::INTEGER)
            )
        END,
        claimed_by = NULL,
        claimed_at = NULL
    WHERE id = p_delivery_id
      AND consumer_group = p_consumer_group
      AND claimed_by = NULLIF(TRIM(COALESCE(p_claim_token, '')), '');

    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.complete_shared_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.complete_shared_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT, INTEGER)
    TO service_role;
