-- Migration 184: preserve attempt_count semantics during retry admission.
-- AutomationJob.mark_failed increments the completed-attempt counter. Queueing
-- a retry must not increment it again; the next failure/success records that run.
CREATE OR REPLACE FUNCTION stage_governed_provider_retry(
    p_job_id TEXT,
    p_recovery_key TEXT,
    p_provider TEXT,
    p_probe_request_id TEXT,
    p_probe_at TIMESTAMPTZ,
    p_probe_ok BOOLEAN
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_job automation_jobs%ROWTYPE;
    v_existing automation_job_recovery_audit%ROWTYPE;
    v_steps JSONB;
    v_has_generation BOOLEAN;
    v_has_publish BOOLEAN;
    v_failed_provider TEXT;
BEGIN
    IF COALESCE(auth.jwt() ->> 'role', '') <> 'service_role' THEN
        RAISE EXCEPTION 'service_role_required' USING ERRCODE = '42501';
    END IF;
    IF COALESCE(BTRIM(p_job_id), '') = ''
       OR COALESCE(BTRIM(p_recovery_key), '') = ''
       OR COALESCE(BTRIM(p_provider), '') = ''
       OR COALESCE(BTRIM(p_probe_request_id), '') = ''
       OR p_probe_ok IS DISTINCT FROM TRUE
       OR p_probe_at IS NULL
       OR p_probe_at > NOW()
       OR p_probe_at < NOW() - INTERVAL '2 minutes' THEN
        RAISE EXCEPTION 'provider_probe_not_fresh_or_successful';
    END IF;

    SELECT * INTO v_existing
      FROM automation_job_recovery_audit
     WHERE recovery_key = p_recovery_key;
    IF FOUND THEN
        IF v_existing.job_id <> p_job_id OR v_existing.action <> 'provider_retry_queued' THEN
            RAISE EXCEPTION 'recovery_idempotency_conflict';
        END IF;
        RETURN jsonb_build_object('status','replayed','job_id',v_existing.job_id,
            'attempt_count',v_existing.new_attempt_count,'recovery_key',v_existing.recovery_key);
    END IF;

    SELECT * INTO v_job
      FROM automation_jobs
     WHERE job_id = p_job_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'recovery_job_not_found'; END IF;

    -- A concurrent replay may have committed while this call waited for the row lock.
    SELECT * INTO v_existing
      FROM automation_job_recovery_audit
     WHERE recovery_key = p_recovery_key;
    IF FOUND THEN
        IF v_existing.job_id <> p_job_id OR v_existing.action <> 'provider_retry_queued' THEN
            RAISE EXCEPTION 'recovery_idempotency_conflict';
        END IF;
        RETURN jsonb_build_object('status','replayed','job_id',v_existing.job_id,
            'attempt_count',v_existing.new_attempt_count,'recovery_key',v_existing.recovery_key);
    END IF;

    IF v_job.status <> 'FAILED'
       OR v_job.last_error_code <> 'AI_PROVIDER_UNAVAILABLE'
       OR v_job.attempt_count >= v_job.max_attempts
       OR v_job.max_attempts < 1
       OR COALESCE(v_job.platform_post_id, '') <> '' THEN
        RAISE EXCEPTION 'job_not_eligible_for_provider_retry';
    END IF;

    v_steps := CASE WHEN jsonb_typeof(v_job.result->'failed_steps') = 'array'
                    THEN v_job.result->'failed_steps' ELSE '[]'::jsonb END;
    SELECT
      COALESCE(bool_or(lower(COALESCE(step->>'step','') || ' ' || COALESCE(step->>'step_name',''))
          ~ '(generate|generation|llm)'), false),
      COALESCE(bool_or(lower(COALESCE(step->>'step','') || ' ' || COALESCE(step->>'step_name',''))
          ~ '(publish|publishing|action_kernel|social_api)'), false),
      MAX(NULLIF(BTRIM(COALESCE(step->>'provider','')), ''))
      INTO v_has_generation, v_has_publish, v_failed_provider
      FROM jsonb_array_elements(v_steps) AS step;
    IF NOT v_has_generation OR v_has_publish
       OR (v_failed_provider IS NOT NULL AND
           lower(v_failed_provider) NOT LIKE '%' || lower(p_provider) || '%'
           AND lower(p_provider) NOT LIKE '%' || lower(v_failed_provider) || '%')
       OR v_job.result ? 'publish_result'
       OR v_job.result ? 'publish_action'
       OR v_job.result ? 'platform_post_id'
       OR v_job.result ? 'action_execution_id' THEN
        RAISE EXCEPTION 'publication_state_not_proven_safe';
    END IF;

    INSERT INTO automation_job_recovery_audit(
        recovery_key, job_id, action, previous_attempt_count, new_attempt_count,
        provider, probe_request_id, probe_at, evidence
    ) VALUES (
        p_recovery_key, p_job_id, 'provider_retry_queued', v_job.attempt_count,
        v_job.attempt_count, p_provider, p_probe_request_id, p_probe_at,
        jsonb_build_object('error_code',v_job.last_error_code,
            'failed_steps',v_steps,'publication_state','no_publish_stage_or_receipt')
    );

    UPDATE automation_jobs
       SET status = 'QUEUED',
           last_error = NULL,
           last_error_code = NULL,
           completed_at = NULL,
           updated_at = NOW()
     WHERE job_id = p_job_id;

    RETURN jsonb_build_object('status','queued','job_id',p_job_id,
        'attempt_count',v_job.attempt_count,'recovery_key',p_recovery_key);
END;
$$;

REVOKE ALL ON FUNCTION stage_governed_provider_retry(TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION stage_governed_provider_retry(TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,BOOLEAN) TO service_role;
