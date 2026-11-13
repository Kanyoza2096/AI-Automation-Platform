-- V69.18 — align Business Automation durable inbox with the canonical events store.
-- This migration is intentionally additive: it does not backfill historical events.
-- New events are fanned out after insert; existing live UnifiedEventBus delivery is unchanged.

CREATE OR REPLACE FUNCTION public.fanout_business_automation_event_delivery()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO public.event_deliveries (
        event_id, consumer_group, event_type, processed
    )
    VALUES (
        NEW.id,
        'business_automation',
        COALESCE(NULLIF(NEW.event_type, ''), NULLIF(NEW.event_name, ''), 'unknown'),
        FALSE
    )
    ON CONFLICT (event_id, consumer_group) DO NOTHING;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.fanout_business_automation_event_delivery() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_business_automation_event_fanout ON public.events;
CREATE TRIGGER trg_business_automation_event_fanout
AFTER INSERT ON public.events
FOR EACH ROW
EXECUTE FUNCTION public.fanout_business_automation_event_delivery();

-- PostgreSQL requires DROP before changing a RETURNS TABLE signature.
DROP FUNCTION IF EXISTS public.claim_business_automation_event_deliveries(TEXT, INTEGER, INTEGER);

CREATE FUNCTION public.claim_business_automation_event_deliveries(
    p_consumer_group TEXT,
    p_limit INTEGER DEFAULT 25,
    p_lease_seconds INTEGER DEFAULT 120
)
RETURNS TABLE (
    delivery_id UUID,
    event_id TEXT,
    event_type TEXT,
    payload JSONB,
    workspace_id TEXT,
    correlation_id TEXT,
    decision_id TEXT,
    brand_id TEXT,
    parent_event_id TEXT,
    claim_token TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_claim TEXT := 'business_automation:' || gen_random_uuid()::text;
    v_lease INTEGER := GREATEST(COALESCE(p_lease_seconds, 120), 30);
BEGIN
    IF p_consumer_group IS DISTINCT FROM 'business_automation' THEN
        RAISE EXCEPTION 'unsupported consumer group';
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT d.id
        FROM public.event_deliveries AS d
        WHERE d.consumer_group = p_consumer_group
          AND d.processed = FALSE
          AND (
              d.claimed_at IS NULL
              OR d.claimed_at < NOW() - make_interval(secs => v_lease)
          )
        ORDER BY d.created_at, d.id
        FOR UPDATE OF d SKIP LOCKED
        LIMIT LEAST(GREATEST(COALESCE(p_limit, 25), 1), 100)
    ),
    claimed AS (
        UPDATE public.event_deliveries AS d
        SET claimed_by = v_claim,
            claimed_at = NOW()
        FROM candidates AS c
        WHERE d.id = c.id
        RETURNING d.id
    )
    SELECT
        d.id,
        e.id::text,
        COALESCE(NULLIF(e.event_type, ''), NULLIF(e.event_name, ''), 'unknown'),
        e.payload,
        e.workspace_id,
        e.correlation_id,
        e.decision_id,
        e.brand_id,
        e.parent_event_id,
        d.claimed_by
    FROM claimed AS c
    JOIN public.event_deliveries AS d ON d.id = c.id
    JOIN public.events AS e ON e.id = d.event_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_business_automation_event_delivery(
    p_delivery_id UUID,
    p_consumer_group TEXT,
    p_claim_token TEXT,
    p_success BOOLEAN,
    p_error TEXT DEFAULT ''
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE public.event_deliveries AS d
    SET processed = COALESCE(p_success, FALSE),
        processed_at = CASE WHEN COALESCE(p_success, FALSE) THEN NOW() ELSE NULL END,
        processing_error = NULLIF(LEFT(COALESCE(p_error, ''), 1000), ''),
        claimed_by = NULL,
        -- A failed delivery waits out a lease before it can be reclaimed.
        claimed_at = CASE WHEN COALESCE(p_success, FALSE) THEN d.claimed_at ELSE NOW() END
    WHERE d.id = p_delivery_id
      AND d.consumer_group = p_consumer_group
      AND d.claimed_by = NULLIF(BTRIM(COALESCE(p_claim_token, '')), '');

    RETURN FOUND;
END;
$$;

-- The unfenced legacy overload must not remain callable by the service role.
REVOKE ALL ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, BOOLEAN, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.claim_business_automation_event_deliveries(TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_business_automation_event_deliveries(TEXT, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_business_automation_event_delivery(UUID, TEXT, TEXT, BOOLEAN, TEXT)
    TO service_role;

CREATE OR REPLACE FUNCTION public.business_automation_event_fanout_ready()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT
        to_regclass('public.events') IS NOT NULL
        AND to_regclass('public.event_deliveries') IS NOT NULL
        AND EXISTS (
            SELECT 1
            FROM pg_trigger AS t
            JOIN pg_class AS c ON c.oid = t.tgrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = 'events'
              AND t.tgname = 'trg_business_automation_event_fanout'
              AND t.tgenabled <> 'D'
              AND t.tgfoid = 'public.fanout_business_automation_event_delivery()'::regprocedure
        )
        AND EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'event_deliveries'
              AND indexname = 'event_deliveries_event_id_consumer_group_key'
        );
$$;

CREATE OR REPLACE FUNCTION public.business_automation_stale_reservation_guard_ready()
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_claim TEXT;
    v_complete TEXT;
BEGIN
    SELECT pg_get_functiondef(
        to_regprocedure('public.claim_business_automation_event_deliveries(text,integer,integer)')
    ) INTO v_claim;
    SELECT pg_get_functiondef(
        to_regprocedure('public.complete_business_automation_event_delivery(uuid,text,text,boolean,text)')
    ) INTO v_complete;

    IF v_claim IS NULL OR v_complete IS NULL THEN
        RETURN FALSE;
    END IF;

    RETURN
        POSITION('FOR UPDATE OF D SKIP LOCKED' IN UPPER(v_claim)) > 0
        AND POSITION('P_LEASE_SECONDS' IN UPPER(v_claim)) > 0
        AND POSITION('CLAIMED_AT' IN UPPER(v_claim)) > 0
        AND POSITION('P_CLAIM_TOKEN' IN UPPER(v_complete)) > 0
        AND POSITION('CLAIMED_BY' IN UPPER(v_complete)) > 0;
END;
$$;

REVOKE ALL ON FUNCTION public.business_automation_event_fanout_ready() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.business_automation_stale_reservation_guard_ready() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.business_automation_event_fanout_ready() TO service_role;
GRANT EXECUTE ON FUNCTION public.business_automation_stale_reservation_guard_ready() TO service_role;
