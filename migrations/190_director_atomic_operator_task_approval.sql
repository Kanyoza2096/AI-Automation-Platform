-- Atomic operator approval for a workspace mission task.
-- The task row, approval provenance, mission snapshot, and append-only event
-- commit or roll back together. Exact replay requires matching fingerprint,
-- actor, timestamp, and canonical task provenance.
CREATE OR REPLACE FUNCTION public.approve_director_mission_task_atomic(
    p_event JSONB,
    p_task_approval JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    mission_row public.director_missions%ROWTYPE;
    task_row public.director_tasks%ROWTYPE;
    existing_event public.director_mission_events%ROWTYPE;
    event_result JSONB;
    approved_at_value TIMESTAMPTZ;
    event_payload JSONB;
BEGIN
    IF p_event IS NULL OR jsonb_typeof(p_event) IS DISTINCT FROM 'object'
       OR p_task_approval IS NULL OR jsonb_typeof(p_task_approval) IS DISTINCT FROM 'object'
       OR p_event->>'p_scope_type' <> 'workspace'
       OR length(COALESCE(p_event->>'p_workspace_id','')) NOT BETWEEN 1 AND 128
       OR length(COALESCE(p_event->>'p_mission_id','')) NOT BETWEEN 1 AND 128
       OR length(COALESCE(p_event->>'p_task_id','')) NOT BETWEEN 1 AND 128
       OR p_event->>'p_event_type' <> 'task.approved'
       OR p_event->>'p_actor_type' <> 'operator'
       OR length(COALESCE(p_event->>'p_actor_id','')) NOT BETWEEN 1 AND 128
       OR length(COALESCE(p_event->>'p_idempotency_key','')) NOT BETWEEN 1 AND 160
       OR length(COALESCE(p_event->>'p_request_fingerprint','')) <> 64
       OR length(COALESCE(p_event->>'p_correlation_id','')) NOT BETWEEN 1 AND 128
       OR p_event->>'p_actor_id' IS DISTINCT FROM p_task_approval->>'approved_by'
       OR p_event->>'p_task_id' IS DISTINCT FROM p_task_approval->>'task_id'
       OR length(COALESCE(p_task_approval->>'approved_by','')) NOT BETWEEN 1 AND 128
       OR length(COALESCE(p_task_approval->>'approved_at','')) = 0
       OR p_event->'p_payload' IS DISTINCT FROM jsonb_build_object(
            'approved_at', p_task_approval->>'approved_at'
       )
       OR jsonb_typeof(p_event->'p_state_snapshot') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_event->'p_evidence_refs') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'director_atomic_task_approval_contract_invalid';
    END IF;

    BEGIN
        approved_at_value := (p_task_approval->>'approved_at')::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'director_atomic_task_approval_timestamp_invalid';
    END;

    SELECT * INTO mission_row
      FROM public.director_missions
     WHERE mission_id = p_event->>'p_mission_id'
       AND scope_type = 'workspace'
       AND workspace_id = p_event->>'p_workspace_id'
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'director_atomic_task_approval_mission_scope_mismatch';
    END IF;

    SELECT * INTO task_row
      FROM public.director_tasks
     WHERE id = p_event->>'p_task_id'
       AND workspace_id = p_event->>'p_workspace_id'
       AND scope_type = 'workspace'
     FOR UPDATE;
    IF NOT FOUND
       OR task_row.payload->>'workspace_id' IS DISTINCT FROM p_event->>'p_workspace_id'
       OR task_row.payload->>'scope_type' IS DISTINCT FROM 'workspace'
       OR task_row.payload->>'mission_id' IS DISTINCT FROM p_event->>'p_mission_id'
       OR NOT (mission_row.task_ids ? (p_event->>'p_task_id')) THEN
        RAISE EXCEPTION 'director_atomic_task_approval_task_scope_mismatch';
    END IF;

    SELECT * INTO existing_event
      FROM public.director_mission_events
     WHERE mission_id = mission_row.mission_id
       AND idempotency_key = p_event->>'p_idempotency_key';

    IF FOUND THEN
        IF existing_event.event_type <> 'task.approved'
           OR existing_event.task_id IS DISTINCT FROM p_event->>'p_task_id'
           OR existing_event.actor_type <> 'operator'
           OR existing_event.actor_id IS DISTINCT FROM p_event->>'p_actor_id'
           OR existing_event.request_fingerprint IS DISTINCT FROM p_event->>'p_request_fingerprint'
           OR existing_event.payload->>'approved_at' IS DISTINCT FROM p_task_approval->>'approved_at'
           OR task_row.status <> 'approved'
           OR task_row.payload->>'approved_by' IS DISTINCT FROM p_task_approval->>'approved_by'
           OR task_row.payload->>'approved_at' IS DISTINCT FROM p_task_approval->>'approved_at' THEN
            RAISE EXCEPTION 'director_atomic_task_approval_replay_conflict';
        END IF;
        RETURN jsonb_build_object(
            'replayed', TRUE,
            'event_id', existing_event.event_id,
            'sequence_number', existing_event.sequence_number,
            'version', mission_row.version,
            'status', mission_row.status,
            'phase', mission_row.phase,
            'task_id', task_row.id,
            'task_status', task_row.status,
            'approved_by', p_task_approval->>'approved_by',
            'approved_at', p_task_approval->>'approved_at'
        );
    END IF;

    IF task_row.status <> 'approval_required'
       OR (mission_row.state_snapshot->'task_status'->>(p_event->>'p_task_id')) IS DISTINCT FROM 'approval_required'
       OR COALESCE(p_event->'p_state_snapshot'->'task_status'->>(p_event->>'p_task_id'),'') <> 'approved' THEN
        RAISE EXCEPTION 'director_atomic_task_approval_state_conflict';
    END IF;

    event_result := public.append_director_mission_event(
        p_event->>'p_scope_type',
        p_event->>'p_workspace_id',
        p_event->>'p_mission_id',
        (p_event->>'p_expected_version')::BIGINT,
        p_event->>'p_idempotency_key',
        p_event->>'p_request_fingerprint',
        p_event->>'p_event_type',
        p_event->>'p_actor_type',
        p_event->>'p_actor_id',
        p_event->>'p_recipient_type',
        p_event->>'p_recipient_id',
        p_event->>'p_task_id',
        p_event->>'p_message_id',
        p_event->>'p_thread_id',
        COALESCE((p_event->>'p_round_number')::INTEGER, 0),
        p_event->>'p_correlation_id',
        p_event->'p_payload',
        p_event->'p_evidence_refs',
        p_event->>'p_status',
        p_event->>'p_phase',
        p_event->'p_state_snapshot'
    );
    IF event_result ? 'error' OR COALESCE((event_result->>'replayed')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'director_atomic_task_approval_event_commit_conflict:%', COALESCE(event_result->>'error','unexpected_replay');
    END IF;

    UPDATE public.director_tasks
       SET status = 'approved',
           payload = jsonb_set(
               jsonb_set(
                   jsonb_set(COALESCE(payload, '{}'::jsonb), '{status}', '"approved"'::jsonb, TRUE),
                   '{approved_by}', to_jsonb(p_task_approval->>'approved_by'), TRUE
               ),
               '{approved_at}', to_jsonb(p_task_approval->>'approved_at'), TRUE
           )
     WHERE id = task_row.id;

    RETURN event_result || jsonb_build_object(
        'task_id', task_row.id,
        'task_status', 'approved',
        'approved_by', p_task_approval->>'approved_by',
        'approved_at', p_task_approval->>'approved_at'
    );
END;
$$;

REVOKE ALL ON FUNCTION public.approve_director_mission_task_atomic(JSONB, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_director_mission_task_atomic(JSONB, JSONB) TO service_role;

COMMENT ON FUNCTION public.approve_director_mission_task_atomic(JSONB, JSONB) IS
    'Atomically commits operator approval provenance to canonical director_tasks and the append-only mission event/snapshot; exact retries require matching fingerprint, actor, and timestamp.';
