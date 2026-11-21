-- Migration 186: bounded failed-job sweep and terminal archive.
-- The live automation_jobs row is removed after three completed failed attempts.
-- A compact immutable snapshot is retained so deletion does not erase the audit trail.
-- Jobs with any indication of an external publish are preserved for reconciliation.

ALTER TABLE automation_job_recovery_audit
    DROP CONSTRAINT IF EXISTS automation_job_recovery_audit_job_id_fkey;

CREATE TABLE IF NOT EXISTS automation_job_recovery_archive (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    workspace_id TEXT,
    brand_id TEXT,
    attempt_count INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    last_error_code TEXT,
    last_error TEXT,
    archived_job JSONB NOT NULL,
    archive_reason TEXT NOT NULL DEFAULT 'three_failed_attempts',
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE automation_job_recovery_archive ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON automation_job_recovery_archive FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON automation_job_recovery_archive TO service_role;
GRANT USAGE, SELECT ON SEQUENCE automation_job_recovery_archive_id_seq TO service_role;

CREATE OR REPLACE FUNCTION sweep_failed_automation_jobs(
    p_batch_size INTEGER DEFAULT 25
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_job automation_jobs%ROWTYPE;
    v_requeued TEXT[] := ARRAY[]::TEXT[];
    v_archived TEXT[] := ARRAY[]::TEXT[];
    v_protected TEXT[] := ARRAY[]::TEXT[];
    v_limit INTEGER := GREATEST(1, LEAST(COALESCE(p_batch_size, 25), 100));
    v_publish_evidence BOOLEAN;
BEGIN
    IF COALESCE(auth.jwt() ->> 'role', '') <> 'service_role' THEN
        RAISE EXCEPTION 'service_role_required' USING ERRCODE = '42501';
    END IF;

    FOR v_job IN
        SELECT *
          FROM automation_jobs
         WHERE status = 'FAILED'
         ORDER BY updated_at ASC
         LIMIT v_limit
         FOR UPDATE SKIP LOCKED
    LOOP
        v_publish_evidence :=
            COALESCE(v_job.platform_post_id, '') <> ''
            OR COALESCE(v_job.result, '{}'::jsonb) ? 'publish_result'
            OR COALESCE(v_job.result, '{}'::jsonb) ? 'publish_action'
            OR COALESCE(v_job.result, '{}'::jsonb) ? 'platform_post_id'
            OR COALESCE(v_job.result, '{}'::jsonb) ? 'action_execution_id'
            OR EXISTS (
                SELECT 1
                  FROM jsonb_array_elements(
                      CASE
                        WHEN jsonb_typeof(v_job.result->'failed_steps') = 'array'
                        THEN v_job.result->'failed_steps'
                        ELSE '[]'::jsonb
                      END
                  ) AS step
                 WHERE lower(COALESCE(step->>'step','') || ' ' ||
                             COALESCE(step->>'step_name',''))
                       ~ '(publish|publishing|action_kernel|social_api)'
            );

        IF v_publish_evidence THEN
            v_protected := array_append(v_protected, v_job.job_id);
            CONTINUE;
        END IF;

        IF v_job.attempt_count >= 3 THEN
            INSERT INTO automation_job_recovery_archive (
                job_id, idempotency_key, workspace_id, brand_id, attempt_count, max_attempts,
                last_error_code, last_error, archived_job
            ) VALUES (
                v_job.job_id, v_job.idempotency_key, v_job.workspace_id, v_job.brand_id,
                v_job.attempt_count, v_job.max_attempts, v_job.last_error_code,
                v_job.last_error, to_jsonb(v_job)
            )
            ON CONFLICT (job_id) DO NOTHING;

            DELETE FROM automation_jobs WHERE id = v_job.id;
            v_archived := array_append(v_archived, v_job.job_id);
        ELSE
            UPDATE automation_jobs
               SET status = 'QUEUED',
                   last_error = NULL,
                   last_error_code = NULL,
                   completed_at = NULL,
                   updated_at = NOW()
             WHERE id = v_job.id;
            v_requeued := array_append(v_requeued, v_job.job_id);
        END IF;
    END LOOP;

    RETURN jsonb_build_object(
        'status', 'completed',
        'requeued', to_jsonb(v_requeued),
        'archived_and_removed', to_jsonb(v_archived),
        'protected_for_publish_reconciliation', to_jsonb(v_protected),
        'requeued_count', cardinality(v_requeued),
        'archived_count', cardinality(v_archived),
        'protected_count', cardinality(v_protected)
    );
END;
$$;

REVOKE ALL ON FUNCTION sweep_failed_automation_jobs(INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION sweep_failed_automation_jobs(INTEGER)
    TO service_role;


-- Preserve the one-job/one-publication idempotency contract after the live row
-- is removed: an archived key must never be recreated by a scheduler or caller.
CREATE OR REPLACE FUNCTION prevent_archived_automation_job_recreation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM automation_job_recovery_archive
         WHERE idempotency_key = NEW.idempotency_key
    ) THEN
        RAISE EXCEPTION 'automation_job_previously_archived'
            USING ERRCODE = '23505';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_prevent_archived_automation_job_recreation
    ON automation_jobs;
CREATE TRIGGER trg_prevent_archived_automation_job_recreation
    BEFORE INSERT ON automation_jobs
    FOR EACH ROW
    EXECUTE FUNCTION prevent_archived_automation_job_recreation();
