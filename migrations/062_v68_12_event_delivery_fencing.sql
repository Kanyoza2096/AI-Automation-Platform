-- V68.12: Business Automation event-delivery ownership fencing.
-- Prevents a stale worker from acknowledging a delivery after its lease has
-- expired and another worker has legitimately claimed it.

CREATE OR REPLACE FUNCTION public.claim_business_automation_event_deliveries(
    p_consumer_group TEXT, p_limit INTEGER DEFAULT 25, p_lease_seconds INTEGER DEFAULT 120)
RETURNS TABLE (delivery_id UUID, event_id TEXT, event_type TEXT, payload JSONB,
               workspace_id TEXT, correlation_id TEXT, decision_id TEXT, brand_id TEXT,
               parent_event_id TEXT, claim_token TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_claim TEXT := 'business_automation:' || gen_random_uuid()::text;
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT d.id FROM public.event_deliveries d
        JOIN public.events e ON e.id = d.event_id
        WHERE d.consumer_group = p_consumer_group AND d.processed = FALSE
          AND (d.claimed_at IS NULL OR d.claimed_at < NOW() - make_interval(secs => GREATEST(p_lease_seconds, 1)))
        ORDER BY d.created_at FOR UPDATE OF d SKIP LOCKED
        LIMIT LEAST(GREATEST(p_limit, 1), 100)
    ), claimed AS (
        UPDATE public.event_deliveries d SET claimed_by = v_claim, claimed_at = NOW()
        FROM candidates c WHERE d.id = c.id RETURNING d.id
    )
    SELECT d.id, e.id, e.event_type, e.payload, e.workspace_id, e.correlation_id,
           e.decision_id, e.brand_id, e.parent_event_id, d.claimed_by
    FROM claimed c JOIN public.event_deliveries d ON d.id = c.id
    JOIN public.events e ON e.id = d.event_id;
END; $$;

CREATE OR REPLACE FUNCTION public.complete_business_automation_event_delivery(
    p_delivery_id UUID, p_consumer_group TEXT, p_claim_token TEXT,
    p_success BOOLEAN, p_error TEXT DEFAULT '')
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    UPDATE public.event_deliveries SET
        processed = p_success,
        processed_at = CASE WHEN p_success THEN NOW() ELSE NULL END,
        processing_error = NULLIF(LEFT(COALESCE(p_error, ''), 1000), ''),
        claimed_by = NULL,
        claimed_at = CASE WHEN p_success THEN claimed_at ELSE NULL END
    WHERE id = p_delivery_id
      AND consumer_group = p_consumer_group
      AND claimed_by = NULLIF(TRIM(COALESCE(p_claim_token, '')), '');
    RETURN FOUND;
END; $$;

REVOKE ALL ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, BOOLEAN, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT) TO service_role;
