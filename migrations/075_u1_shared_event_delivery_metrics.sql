-- U1: aggregated delivery health metrics for trusted internal observability.
-- Returns aggregate counts and latency only; no event payloads or tenant identifiers.
CREATE OR REPLACE FUNCTION public.get_shared_event_delivery_metrics(
    p_consumer_group TEXT DEFAULT NULL
)
RETURNS TABLE (
    consumer_group TEXT,
    total_deliveries BIGINT,
    pending_count BIGINT,
    due_count BIGINT,
    scheduled_retry_count BIGINT,
    in_flight_count BIGINT,
    completed_count BIGINT,
    dead_letter_count BIGINT,
    retrying_count BIGINT,
    retry_rate_percent NUMERIC,
    average_delivery_latency_ms NUMERIC,
    p95_delivery_latency_ms NUMERIC,
    oldest_pending_age_seconds NUMERIC
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT
        d.consumer_group,
        COUNT(*)::BIGINT,
        COUNT(*) FILTER (
            WHERE NOT d.processed AND d.dead_lettered_at IS NULL
        )::BIGINT,
        COUNT(*) FILTER (
            WHERE NOT d.processed
              AND d.dead_lettered_at IS NULL
              AND d.next_attempt_at <= NOW()
              AND (d.claimed_at IS NULL OR d.claimed_at < NOW() - INTERVAL '5 minutes')
        )::BIGINT,
        COUNT(*) FILTER (
            WHERE NOT d.processed
              AND d.dead_lettered_at IS NULL
              AND d.next_attempt_at > NOW()
        )::BIGINT,
        COUNT(*) FILTER (
            WHERE NOT d.processed
              AND d.dead_lettered_at IS NULL
              AND d.claimed_at >= NOW() - INTERVAL '5 minutes'
        )::BIGINT,
        COUNT(*) FILTER (WHERE d.processed)::BIGINT,
        COUNT(*) FILTER (WHERE d.dead_lettered_at IS NOT NULL)::BIGINT,
        COUNT(*) FILTER (
            WHERE d.attempt_count > 0
              AND NOT d.processed
              AND d.dead_lettered_at IS NULL
        )::BIGINT,
        COALESCE(
            100.0 * COUNT(*) FILTER (WHERE d.attempt_count > 0)
            / NULLIF(COUNT(*), 0),
            0
        )::NUMERIC,
        (
            AVG(EXTRACT(EPOCH FROM (d.processed_at - e.created_at)) * 1000)
            FILTER (WHERE d.processed AND d.processed_at IS NOT NULL)
        )::NUMERIC,
        (
            percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (d.processed_at - e.created_at)) * 1000
            ) FILTER (WHERE d.processed AND d.processed_at IS NOT NULL)
        )::NUMERIC,
        COALESCE(
            EXTRACT(EPOCH FROM (
                NOW() - (MIN(e.created_at) FILTER (
                    WHERE NOT d.processed AND d.dead_lettered_at IS NULL
                ))
            )),
            0
        )::NUMERIC
    FROM public.event_deliveries AS d
    JOIN public.events AS e ON e.id = d.event_id
    WHERE p_consumer_group IS NULL OR d.consumer_group = p_consumer_group
    GROUP BY d.consumer_group;
$$;

REVOKE ALL ON FUNCTION public.get_shared_event_delivery_metrics(TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_shared_event_delivery_metrics(TEXT)
    TO service_role;
