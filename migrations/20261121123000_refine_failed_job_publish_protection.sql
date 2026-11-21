-- Refine failed-job protection so pre-publication policy blocks are terminal failures,
-- not uncertain external publication attempts. STALE_JOB and explicit provider/action
-- evidence remain protected for reconciliation.
CREATE OR REPLACE FUNCTION public.sweep_failed_automation_jobs(
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
          FROM public.automation_jobs
         WHERE status = 'FAILED'
         ORDER BY updated_at ASC
         LIMIT v_limit
         FOR UPDATE SKIP LOCKED
    LOOP
        -- A stale worker may have completed an external publish without persisting
        -- the provider result. Explicit persisted provider/action evidence is also
        -- protected. A publish step blocked by validation/mandate never reached the
        -- provider and is therefore safe to archive at the retry limit.
        v_publish_evidence :=
            v_job.last_error_code = 'STALE_JOB'
            OR COALESCE(v_job.platform_post_id, '') <> ''
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
                   AND COALESCE(step->>'error_code', '') NOT IN (
                       'publish_validation_not_approved',
                       'council_needs_revision'
                   )
                   AND lower(COALESCE(step->>'error', ''))
                       !~ '(blocked.*(validation|mandate|approval)|validation.*not.*approved|insufficient mandate)'
            );

        IF v_publish_evidence THEN
            v_protected := array_append(v_protected, v_job.job_id);
            CONTINUE;
        END IF;

        IF v_job.attempt_count >= 3 THEN
            INSERT INTO public.automation_job_recovery_archive (
                job_id, idempotency_key, workspace_id, brand_id, attempt_count, max_attempts,
                last_error_code, last_error, archived_job
            ) VALUES (
                v_job.job_id, v_job.idempotency_key, v_job.workspace_id, v_job.brand_id,
                v_job.attempt_count, v_job.max_attempts, v_job.last_error_code,
                v_job.last_error, to_jsonb(v_job)
            )
            ON CONFLICT (job_id) DO NOTHING;

            DELETE FROM public.automation_jobs WHERE id = v_job.id;
            v_archived := array_append(v_archived, v_job.job_id);
        ELSE
            UPDATE public.automation_jobs
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

REVOKE ALL ON FUNCTION public.sweep_failed_automation_jobs(INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sweep_failed_automation_jobs(INTEGER)
    TO service_role;
