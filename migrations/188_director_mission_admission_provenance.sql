-- Seed the complete bounded orchestration state at mission admission.
-- This replaces the v2 admission function from migration 176 without changing
-- the legacy task/admission contract or existing mission/event rows.

CREATE OR REPLACE FUNCTION public.stage_director_mission_v2(
    p_workspace_id TEXT,
    p_idempotency_key TEXT,
    p_plan_id TEXT,
    p_mission_id TEXT,
    p_request_fingerprint TEXT,
    p_tasks JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    admission_result JSONB;
    mission_row public.director_missions%ROWTYPE;
    event_result JSONB;
    initial_state JSONB;
    initial_task_status JSONB;
    source_collaboration_run_id TEXT;
    source_collaboration_run_count INTEGER := 0;
    source_collaboration_provenance_sha256 TEXT;
    source_collaboration_provenance_count INTEGER := 0;
BEGIN
    admission_result := public.stage_director_mission(
        p_workspace_id, p_idempotency_key, p_plan_id,
        p_mission_id, p_request_fingerprint, p_tasks
    );

    IF admission_result ? 'error' THEN
        RETURN admission_result;
    END IF;

    -- Carry the same collaboration origin already persisted on each admitted
    -- task into the immutable mission.admitted journal event. Reject mixed
    -- origins rather than recording an ambiguous mission-level provenance.
    SELECT count(DISTINCT substring(source_value FROM length('collaboration_run:') + 1)),
           min(substring(source_value FROM length('collaboration_run:') + 1))
      INTO source_collaboration_run_count, source_collaboration_run_id
      FROM jsonb_array_elements_text(admission_result->'task_ids') AS admitted(task_id)
      JOIN public.director_tasks AS admitted_task
        ON admitted_task.id = admitted.task_id
       AND admitted_task.workspace_id = p_workspace_id
       AND admitted_task.scope_type = 'workspace'
      CROSS JOIN LATERAL jsonb_array_elements_text(
          COALESCE(admitted_task.payload->'sources', '[]'::jsonb)
      ) AS sources(source_value)
     WHERE admitted_task.payload->>'mission_id' = p_mission_id
       AND source_value LIKE 'collaboration_run:%';

    IF source_collaboration_run_count > 1 THEN
        RAISE EXCEPTION 'director_mission_collaboration_provenance_conflict';
    END IF;

    SELECT count(DISTINCT substring(source_value FROM length('collaboration_provenance_sha256:') + 1)),
           min(substring(source_value FROM length('collaboration_provenance_sha256:') + 1))
      INTO source_collaboration_provenance_count, source_collaboration_provenance_sha256
      FROM jsonb_array_elements_text(admission_result->'task_ids') AS admitted(task_id)
      JOIN public.director_tasks AS admitted_task
        ON admitted_task.id = admitted.task_id
       AND admitted_task.workspace_id = p_workspace_id
       AND admitted_task.scope_type = 'workspace'
      CROSS JOIN LATERAL jsonb_array_elements_text(
          COALESCE(admitted_task.payload->'sources', '[]'::jsonb)
      ) AS sources(source_value)
     WHERE admitted_task.payload->>'mission_id' = p_mission_id
       AND source_value LIKE 'collaboration_provenance_sha256:%';

    IF source_collaboration_provenance_count > 1 THEN
        RAISE EXCEPTION 'director_mission_collaboration_digest_conflict';
    END IF;

    IF source_collaboration_provenance_sha256 IS NOT NULL
       AND source_collaboration_run_id IS NULL THEN
        RAISE EXCEPTION 'director_mission_collaboration_digest_without_run';
    END IF;

    SELECT COALESCE(
        jsonb_object_agg(task_id, to_jsonb('approval_required'::TEXT)),
        '{}'::jsonb
    )
      INTO initial_task_status
      FROM jsonb_array_elements_text(admission_result->'task_ids') AS task_ids(task_id);

    initial_state := jsonb_build_object(
        'task_ids', admission_result->'task_ids',
        'task_status', initial_task_status,
        'task_assignments', '{}'::jsonb,
        'engine_progress', '{}'::jsonb,
        'task_results', '{}'::jsonb,
        'evidence_refs', '[]'::jsonb,
        'unresolved_disagreements', '[]'::jsonb,
        'pending_handoffs', '{}'::jsonb,
        'pending_questions', '{}'::jsonb,
        'active_reasoning_round', 0,
        'selected_engines', '[]'::jsonb,
        'budget', jsonb_build_object(
            'max_rounds', 3,
            'rounds_used', 0,
            'tokens_used', 0,
            'provider_calls', 0,
            'max_tokens', NULL,
            'max_provider_calls', NULL,
            'exhausted', FALSE
        )
    );

    INSERT INTO public.director_missions (
        mission_id, scope_type, workspace_id, plan_id,
        admission_idempotency_key, task_ids, status, phase, version, state_snapshot
    ) VALUES (
        p_mission_id, 'workspace', p_workspace_id, p_plan_id,
        p_idempotency_key, admission_result->'task_ids',
        'admitted', 'admission', 0, initial_state
    ) ON CONFLICT (mission_id) DO NOTHING;

    SELECT * INTO mission_row
      FROM public.director_missions
     WHERE mission_id = p_mission_id
     FOR UPDATE;

    IF NOT FOUND
       OR mission_row.scope_type <> 'workspace'
       OR mission_row.workspace_id <> p_workspace_id
       OR mission_row.plan_id <> p_plan_id
       OR mission_row.admission_idempotency_key <> p_idempotency_key THEN
        RAISE EXCEPTION 'director_mission_identity_conflict';
    END IF;

    event_result := public.append_director_mission_event(
        'workspace',
        p_workspace_id,
        p_mission_id,
        0,
        'mission-admission:' || p_idempotency_key,
        p_request_fingerprint,
        'mission.admitted',
        'director',
        'director',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        0,
        p_mission_id,
        jsonb_build_object('plan_id', p_plan_id, 'task_ids', admission_result->'task_ids')
            || CASE WHEN source_collaboration_run_id IS NOT NULL THEN jsonb_build_object('source_collaboration_run_id', source_collaboration_run_id) ELSE '{}'::jsonb END
            || CASE WHEN source_collaboration_provenance_sha256 IS NOT NULL THEN jsonb_build_object('source_collaboration_provenance_sha256', source_collaboration_provenance_sha256) ELSE '{}'::jsonb END,
        '[]'::jsonb,
        'admitted',
        'admission',
        initial_state
    );

    IF event_result ? 'error' THEN
        RAISE EXCEPTION 'director_mission_initial_event_failed:%', event_result->>'error';
    END IF;

    RETURN admission_result || jsonb_build_object(
        'mission_version', event_result->'version',
        'mission_status', event_result->'status'
    );
END;
$body$;

COMMENT ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) IS
    'Privileged, fixed-search-path RPC. Atomically admits workspace tasks and initializes the full bounded Director mission state/event journal; callable only by service_role.';
