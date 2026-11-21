-- Durable platform-scoped Director recovery mission admission.
-- Recovery missions are not attached to a workspace strategic plan. They are
-- admitted through a dedicated idempotent RPC and remain advisory-only.
ALTER TABLE public.director_missions
    ALTER COLUMN plan_id DROP NOT NULL;

ALTER TABLE public.director_missions
    DROP CONSTRAINT IF EXISTS director_missions_scope_contract;
ALTER TABLE public.director_missions
    ADD CONSTRAINT director_missions_scope_contract CHECK (
        (scope_type = 'workspace' AND workspace_id IS NOT NULL
            AND admission_idempotency_key IS NOT NULL AND plan_id IS NOT NULL)
        OR
        (scope_type = 'platform' AND workspace_id IS NULL
            AND admission_idempotency_key IS NULL AND plan_id IS NULL)
    );

CREATE TABLE IF NOT EXISTS public.director_platform_recovery_admissions (
    idempotency_key TEXT PRIMARY KEY
        CHECK (length(idempotency_key) BETWEEN 1 AND 160),
    mission_id TEXT NOT NULL UNIQUE
        REFERENCES public.director_missions(mission_id) DEFERRABLE INITIALLY DEFERRED,
    request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
    task_ids JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(task_ids) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.director_platform_recovery_admissions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_platform_recovery_admissions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.director_platform_recovery_admissions TO service_role;

CREATE OR REPLACE FUNCTION public.stage_director_platform_recovery_mission(
    p_idempotency_key TEXT,
    p_mission_id TEXT,
    p_request_fingerprint TEXT,
    p_correlation_id TEXT,
    p_tasks JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    existing public.director_platform_recovery_admissions%ROWTYPE;
    task_row JSONB;
    ids JSONB := '[]'::jsonb;
    initial_state JSONB;
    event_result JSONB;
    task_count INTEGER;
BEGIN
    IF p_idempotency_key IS NULL OR length(trim(p_idempotency_key)) NOT BETWEEN 1 AND 160
       OR p_mission_id IS NULL OR length(trim(p_mission_id)) NOT BETWEEN 1 AND 128
       OR p_request_fingerprint IS NULL OR length(p_request_fingerprint) <> 64
       OR p_correlation_id IS NULL OR length(trim(p_correlation_id)) NOT BETWEEN 1 AND 128
       OR p_tasks IS NULL OR jsonb_typeof(p_tasks) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'platform_recovery_admission_input_invalid';
    END IF;

    task_count := jsonb_array_length(p_tasks);
    IF task_count < 1 OR task_count > 12 THEN
        RAISE EXCEPTION 'platform_recovery_task_count_invalid';
    END IF;

    INSERT INTO public.director_platform_recovery_admissions (
        idempotency_key, mission_id, request_fingerprint, task_ids
    ) VALUES (p_idempotency_key, p_mission_id, p_request_fingerprint, '[]'::jsonb)
    ON CONFLICT (idempotency_key) DO NOTHING;

    IF NOT FOUND THEN
        SELECT * INTO existing
          FROM public.director_platform_recovery_admissions
         WHERE idempotency_key = p_idempotency_key
         FOR UPDATE;
        IF existing.request_fingerprint <> p_request_fingerprint
           OR existing.mission_id <> p_mission_id THEN
            RETURN jsonb_build_object('error', 'platform_recovery_idempotency_conflict');
        END IF;
        RETURN jsonb_build_object(
            'mission_id', existing.mission_id,
            'task_ids', existing.task_ids,
            'replayed', TRUE
        );
    END IF;

    IF (SELECT count(DISTINCT value->>'id') FROM jsonb_array_elements(p_tasks)) <> task_count
       OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_tasks) AS t(value)
             WHERE COALESCE(t.value->>'id', '') = ''
                OR t.value->>'scope_type' IS DISTINCT FROM 'platform'
                OR t.value->>'status' IS DISTINCT FROM 'approval_required'
                OR t.value->>'workspace_id' IS NOT NULL
                OR jsonb_typeof(t.value->'payload') IS DISTINCT FROM 'object'
                OR t.value->'payload'->>'id' IS DISTINCT FROM t.value->>'id'
                OR t.value->'payload'->>'scope_type' IS DISTINCT FROM 'platform'
                OR t.value->'payload'->>'status' IS DISTINCT FROM 'approval_required'
                OR t.value->'payload'->>'mission_id' IS DISTINCT FROM p_mission_id
                OR t.value->'payload'->>'workspace_id' IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'platform_recovery_task_contract_invalid';
    END IF;

    FOR task_row IN SELECT value FROM jsonb_array_elements(p_tasks)
    LOOP
        INSERT INTO public.director_tasks (
            id, workspace_id, scope_type, status, payload, created_at
        ) VALUES (
            task_row->>'id', NULL, 'platform', 'approval_required',
            task_row->'payload',
            COALESCE((task_row->>'created_at')::timestamptz, NOW())
        );
        ids := ids || jsonb_build_array(task_row->>'id');
    END LOOP;

    initial_state := jsonb_build_object(
        'task_ids', ids,
        'active_reasoning_round', 0,
        'unresolved_disagreements', '[]'::jsonb,
        'selected_engines', '[]'::jsonb,
        'recovery', jsonb_build_object(
            'kind', 'automation_job_failure_sweep',
            'automatic_retry', FALSE,
            'publication_replay_allowed', FALSE
        )
    );

    INSERT INTO public.director_missions (
        mission_id, scope_type, workspace_id, plan_id,
        admission_idempotency_key, task_ids, status, phase, version, state_snapshot
    ) VALUES (
        p_mission_id, 'platform', NULL, NULL, NULL, ids,
        'admitted', 'recovery', 0, initial_state
    );

    UPDATE public.director_platform_recovery_admissions
       SET task_ids = ids
     WHERE idempotency_key = p_idempotency_key;

    event_result := public.append_director_mission_event(
        'platform', NULL, p_mission_id, 0,
        'platform-recovery-admission:' || p_idempotency_key,
        p_request_fingerprint, 'mission.admitted', 'director', 'director',
        NULL, NULL, NULL, NULL, NULL, 0, p_correlation_id,
        jsonb_build_object(
            'kind', 'automation_job_failure_sweep',
            'task_ids', ids,
            'automatic_retry', FALSE,
            'publication_replay_allowed', FALSE
        ),
        '[]'::jsonb, 'admitted', 'recovery', initial_state
    );

    IF event_result ? 'error' THEN
        RAISE EXCEPTION 'platform_recovery_mission_event_failed:%', event_result->>'error';
    END IF;

    RETURN jsonb_build_object(
        'mission_id', p_mission_id,
        'task_ids', ids,
        'replayed', FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION public.stage_director_platform_recovery_mission(TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.stage_director_platform_recovery_mission(TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

COMMENT ON FUNCTION public.stage_director_platform_recovery_mission(TEXT, TEXT, TEXT, TEXT, JSONB) IS
    'Idempotently admits a platform-scoped advisory recovery mission. Specialist work cannot retry jobs or replay publication.';
