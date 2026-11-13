-- Remove a duplicate pg_cron stale-job reaper only when the canonical
-- reap-stale-to-retry schedule is present. This is intentionally idempotent:
-- it does not create or alter the canonical reaper and does not touch job rows.
--
-- Production inspection on 2026-11-13 found both:
--   reap-stale-to-retry  (*/1 * * * *)
--   reap-stale-split     (*/1 * * * *)
-- with identical SQL bodies. Both executed every minute, duplicating the
-- retry/terminal-state transition work.

DO $$
DECLARE
    duplicate_job record;
BEGIN
    IF to_regclass('cron.job') IS NOT NULL
       AND to_regprocedure('cron.unschedule(bigint)') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM cron.job WHERE jobname = 'reap-stale-to-retry'
       )
    THEN
        FOR duplicate_job IN
            SELECT jobid
            FROM cron.job
            WHERE jobname = 'reap-stale-split'
        LOOP
            PERFORM cron.unschedule(duplicate_job.jobid);
        END LOOP;
    END IF;
END;
$$;
