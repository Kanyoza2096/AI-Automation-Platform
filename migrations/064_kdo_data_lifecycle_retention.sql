-- KDO data lifecycle management.
-- Keep the Director's operational memory bounded without deleting incident evidence.
-- Only old, clean nominal observations are eligible; non-nominal, unknown,
-- malformed, and issue-bearing observations are retained.

CREATE INDEX IF NOT EXISTS idx_director_observations_timestamp
    ON public.director_observations (timestamp);

CREATE OR REPLACE FUNCTION public.kdo_prune_director_observations(
    p_retention interval DEFAULT interval '30 days',
    p_batch_size integer DEFAULT 5000
) RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted integer := 0;
    v_batch integer := 0;
BEGIN
    IF p_batch_size < 1 OR p_batch_size > 20000 THEN
        RAISE EXCEPTION 'p_batch_size must be between 1 and 20000';
    END IF;

    LOOP
        WITH candidates AS (
            SELECT id
            FROM public.director_observations
            WHERE timestamp < now() - p_retention
              AND jsonb_typeof(payload) = 'string'
              AND (payload #>> '{}')::jsonb ->> 'status' = 'nominal'
              AND CASE
                    WHEN ((payload #>> '{}')::jsonb)->'issues' IS NULL THEN true
                    WHEN jsonb_typeof(((payload #>> '{}')::jsonb)->'issues') = 'array'
                      THEN jsonb_array_length(((payload #>> '{}')::jsonb)->'issues') = 0
                    ELSE false
                  END
            ORDER BY timestamp ASC
            LIMIT p_batch_size
        )
        DELETE FROM public.director_observations d
        USING candidates c
        WHERE d.id = c.id;

        GET DIAGNOSTICS v_batch = ROW_COUNT;
        v_deleted := v_deleted + v_batch;
        EXIT WHEN v_batch = 0 OR v_deleted >= p_batch_size;
    END LOOP;

    RETURN v_deleted;
END;
$$;

-- Remove the previously verified duplicate stale-to-retry cron entry only
-- when the canonical retry job is still present at the same cadence.
DO $$
DECLARE
    duplicate_job_id bigint;
BEGIN
    IF EXISTS (
        SELECT 1 FROM cron.job
        WHERE jobname = 'reap-stale-to-retry'
          AND schedule = '*/1 * * * *'
    ) THEN
        SELECT jobid INTO duplicate_job_id
        FROM cron.job
        WHERE jobname = 'reap-stale-split'
          AND schedule = '*/1 * * * *'
          AND command ILIKE '%RETRY_PENDING%'
          AND command ILIKE '%retries exhausted%'
        LIMIT 1;

        IF duplicate_job_id IS NOT NULL THEN
            PERFORM cron.unschedule(duplicate_job_id);
        END IF;
    END IF;
END;
$$;

DELETE FROM cron.job_run_details
WHERE end_time < now() - interval '14 days';

SELECT cron.unschedule(jobid)
FROM cron.job
WHERE jobname IN (
    'kdo-cron-history-retention',
    'kdo-director-observation-retention'
);

SELECT cron.schedule(
    'kdo-cron-history-retention',
    '20 3 * * *',
    $$DELETE FROM cron.job_run_details WHERE end_time < now() - interval '14 days'$$
);

SELECT cron.schedule(
    'kdo-director-observation-retention',
    '35 3 * * *',
    $$SELECT public.kdo_prune_director_observations(interval '30 days', 20000)$$
);
