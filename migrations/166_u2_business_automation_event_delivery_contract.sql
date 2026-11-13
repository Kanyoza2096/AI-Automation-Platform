-- U2: align the durable Business Automation consumer with the producer queue.
--
-- Migration 164 writes one row per subscribed event into public.event_deliveries.
-- The deployed claim RPC was still polling the older business_event_deliveries
-- queue, so the producer and consumer could never meet. This migration makes
-- the claim RPC consume the exact table populated by migration 164.
--
-- Claim ownership is a per-batch token stored in claimed_by. Workspace identity
-- is derived from the joined event row, never supplied by the consumer.

CREATE OR REPLACE FUNCTION public.claim_business_automation_event_deliveries(
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
    claim_token TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_claim TEXT := 'business_automation:' || gen_random_uuid()::text;
    v_lease INTEGER := GREATEST(COALESCE(p_lease_seconds, 120), 30);
BEGIN
    IF p_consumer_group IS NULL OR length(trim(p_consumer_group)) = 0 THEN
        RAISE EXCEPTION 'consumer_group is required';
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT d.id
        FROM public.event_deliveries AS d
        JOIN public.events AS e
          ON e.id = d.event_id
        WHERE d.consumer_group = p_consumer_group
          AND d.processed IS FALSE
          AND (
              d.claimed_by IS NULL
              OR d.claimed_at < NOW() - make_interval(secs => v_lease)
          )
          AND e.workspace_id IS NOT NULL
          AND length(trim(e.workspace_id)) > 0
          AND e.event_type IS NOT NULL
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
        d.id AS delivery_id,
        e.id::TEXT AS event_id,
        e.event_type,
        COALESCE(e.payload, '{}'::JSONB) AS payload,
        e.workspace_id,
        e.correlation_id,
        v_claim AS claim_token
    FROM claimed AS c
    JOIN public.event_deliveries AS d
      ON d.id = c.id
    JOIN public.events AS e
      ON e.id = d.event_id;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_business_automation_event_deliveries(TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_business_automation_event_deliveries(TEXT, INTEGER, INTEGER)
    TO service_role;

-- Readiness is an end-to-end contract check, not merely proof that a trigger
-- exists. It stays false unless the trigger, claim RPC and workspace-fenced
-- completion RPC all target the same events/event_deliveries queue.
CREATE OR REPLACE FUNCTION public.business_automation_event_fanout_ready()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT
        EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger AS trigger_row
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_row.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relname = 'events'
              AND trigger_row.tgname = 'trg_fanout_business_automation_event_delivery'
              AND trigger_row.tgfoid = 'public.fanout_business_automation_event_delivery()'::regprocedure
              AND trigger_row.tgisinternal IS FALSE
              AND trigger_row.tgenabled IN ('O', 'A')
        )
        AND EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = 'claim_business_automation_event_deliveries'
              AND pg_catalog.pg_get_function_identity_arguments(p.oid)
                  = 'p_consumer_group text, p_limit integer, p_lease_seconds integer'
              AND p.prosecdef IS TRUE
              AND p.prosrc LIKE '%public.event_deliveries%'
              AND p.prosrc LIKE '%public.events%'
              AND p.prosrc NOT LIKE '%business_event_deliveries%'
        )
        AND EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname = 'complete_business_automation_event_delivery'
              AND pg_catalog.pg_get_function_identity_arguments(p.oid)
                  = 'p_delivery_id uuid, p_workspace_id text, p_consumer_group text, p_claim_token text, p_success boolean, p_error text'
              AND p.prosecdef IS TRUE
              AND p.prosrc LIKE '%public.event_deliveries%'
              AND p.prosrc LIKE '%public.events%'
              AND p.prosrc LIKE '%p_claim_token%'
        );
$$;

REVOKE ALL ON FUNCTION public.business_automation_event_fanout_ready()
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.business_automation_event_fanout_ready()
    TO service_role;
