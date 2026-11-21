-- Permanently retire the legacy minute-by-minute automation retry cascade.
--
-- Production incident (2026-11-21): three pg_cron jobs independently moved
-- active jobs to STALE, STALE to RETRY_PENDING, and RETRY_PENDING to QUEUED.
-- The last transition bypassed the bounded failed-job sweep and could restart
-- AI content generation repeatedly. The schedules were removed operationally;
-- this migration makes that removal reproducible on future database rebuilds.
--
-- Intentionally do NOT remove the in-process stale detector. It only marks a
-- stuck job STALE; it does not replay publication or enqueue content generation.

DO $$
DECLARE
    v_job RECORD;
BEGIN
    IF to_regclass('cron.job') IS NULL
       OR to_regprocedure('cron.unschedule(bigint)') IS NULL THEN
        RETURN;
    END IF;

    FOR v_job IN
        SELECT jobid
          FROM cron.job
         WHERE jobname IN (
             'reap-active-to-stale',
             'reap-stale-to-retry',
             'reap-retry-to-queued'
         )
    LOOP
        PERFORM cron.unschedule(v_job.jobid);
    END LOOP;
END;
$$;

-- A stale worker timeout is not proof that publishing failed. Require
-- reconciliation before any stale record can be automatically requeued.
CREATE OR REPLACE FUNCTION public.prevent_unreconciled_stale_job_requeue()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.status = 'STALE'
       AND OLD.last_error_code = 'STALE_JOB'
       AND NEW.status IN ('RETRY_PENDING', 'QUEUED') THEN
        RAISE EXCEPTION
            'stale_automation_job_requires_publish_reconciliation'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_prevent_unreconciled_stale_job_requeue
    ON public.automation_jobs;

CREATE TRIGGER trg_prevent_unreconciled_stale_job_requeue
    BEFORE UPDATE OF status ON public.automation_jobs
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_unreconciled_stale_job_requeue();

COMMENT ON FUNCTION public.prevent_unreconciled_stale_job_requeue() IS
'Blocks automatic requeue of STALE_JOB records until external publish outcome has been reconciled.';
