-- Atomically persist mission lifecycle events and canonical Director task status.
-- This closes the partial-commit window between the mission journal and director_tasks.
-- The canonical director_tasks table has no updated_at column; do not write one.

CREATE OR REPLACE FUNCTION public.append_director_mission_event_with_task_sync(
    p_event JSONB,
    p_task_sync JSONB DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
DECLARE
    event_result JSONB;
    task_row public.director_tasks%ROWTYPE;
    target_status TEXT;
    expected_statuses JSONB;
    next_payload JSONB;
    affected INTEGER;
BEGIN
    IF p_event IS NULL OR jsonb_typeof(p_event) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'director_event_payload_invalid';
    END IF;
    IF p_task_sync IS NOT NULL AND jsonb_typeof(p_task_sync) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'director_task_sync_payload_invalid';
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
        (p_event->>'p_round_number')::INTEGER,
        p_event->>'p_correlation_id',
        p_event->'p_payload',
        p_event->'p_evidence_refs',
        p_event->>'p_status',
        p_event->>'p_phase',
        p_event->'p_state_snapshot'
    );

    IF event_result ? 'error' OR p_task_sync IS NULL THEN
        RETURN event_result;
    END IF;

    IF p_event->>'p_scope_type' <> 'workspace'
       OR NULLIF(p_event->>'p_workspace_id', '') IS NULL
       OR NULLIF(p_event->>'p_task_id', '') IS NULL THEN
        RAISE EXCEPTION 'atomic_task_sync_requires_workspace_task_event';
    END IF;

    target_status := p_task_sync->>'target_status';
    expected_statuses := p_task_sync->'expected_statuses';
    IF target_status IS NULL OR target_status NOT IN (
        'approval_required', 'approved', 'executing', 'completed',
        'failed', 'blocked', 'flagged', 'verified'
    ) OR jsonb_typeof(expected_statuses) IS DISTINCT FROM 'array'
       OR jsonb_array_length(expected_statuses) = 0 THEN
        RAISE EXCEPTION 'atomic_task_sync_contract_invalid';
    END IF;

    SELECT * INTO task_row
      FROM public.director_tasks
     WHERE id = p_event->>'p_task_id'
       AND workspace_id = p_event->>'p_workspace_id'
       AND scope_type = 'workspace'
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'atomic_task_sync_task_not_found';
    END IF;

    IF task_row.payload->>'workspace_id' IS DISTINCT FROM p_event->>'p_workspace_id'
       OR task_row.payload->>'scope_type' IS DISTINCT FROM 'workspace'
       OR task_row.payload->>'mission_id' IS DISTINCT FROM p_event->>'p_mission_id'
       OR task_row.payload->>'status' IS DISTINCT FROM task_row.status THEN
        RAISE EXCEPTION 'atomic_task_sync_scope_or_payload_mismatch';
    END IF;

    IF task_row.status = target_status THEN
        RETURN event_result || jsonb_build_object('task_status', target_status, 'task_sync_replayed', TRUE);
    END IF;

    IF NOT expected_statuses ? task_row.status THEN
        RAISE EXCEPTION 'atomic_task_sync_status_conflict:%->%', task_row.status, target_status;
    END IF;

    next_payload := jsonb_set(task_row.payload, '{status}', to_jsonb(target_status), TRUE);
    IF p_task_sync ? 'result' AND p_task_sync->'result' IS NOT NULL THEN
        next_payload := jsonb_set(next_payload, '{result}', p_task_sync->'result', TRUE);
    END IF;
    IF p_task_sync ? 'error' THEN
        IF p_task_sync->'error' IS NULL OR p_task_sync->'error' = 'null'::jsonb THEN
            next_payload := jsonb_set(next_payload, '{error}', 'null'::jsonb, TRUE);
        ELSE
            next_payload := jsonb_set(next_payload, '{error}', p_task_sync->'error', TRUE);
        END IF;
    ELSIF target_status IN ('approved', 'executing', 'completed', 'verified', 'approval_required') THEN
        next_payload := jsonb_set(next_payload, '{error}', 'null'::jsonb, TRUE);
    END IF;

    UPDATE public.director_tasks
       SET status = target_status,
           payload = next_payload
     WHERE id = task_row.id
       AND workspace_id = task_row.workspace_id
       AND scope_type = 'workspace'
       AND status = task_row.status;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN
        RAISE EXCEPTION 'atomic_task_sync_update_not_acknowledged';
    END IF;

    RETURN event_result || jsonb_build_object('task_status', target_status, 'task_sync_replayed', FALSE);
END;
$function$;

REVOKE ALL ON FUNCTION public.append_director_mission_event_with_task_sync(JSONB, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_director_mission_event_with_task_sync(JSONB, JSONB) FROM anon;
REVOKE ALL ON FUNCTION public.append_director_mission_event_with_task_sync(JSONB, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.append_director_mission_event_with_task_sync(JSONB, JSONB) TO service_role;

COMMENT ON FUNCTION public.append_director_mission_event_with_task_sync(JSONB, JSONB) IS
    'Atomically appends a versioned Director mission event and synchronizes its canonical workspace task status in the same transaction.';
