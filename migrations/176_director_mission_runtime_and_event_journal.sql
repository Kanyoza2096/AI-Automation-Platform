-- Durable Director mission aggregate and append-only collaboration journal.
-- The existing director_tasks table remains the canonical task store.
-- The existing Event Bus remains a notification/fan-out mechanism, not the
-- authoritative mission journal.

CREATE TABLE IF NOT EXISTS public.director_missions (
    mission_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('workspace', 'platform')),
    workspace_id TEXT,
    plan_id TEXT NOT NULL REFERENCES public.director_strategic_plans(plan_id) ON DELETE RESTRICT,
    admission_idempotency_key TEXT,
    task_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'admitted'
        CHECK (status IN ('admitted', 'queued', 'running', 'awaiting_review', 'paused',
                          'completed', 'partial', 'failed', 'cancelled', 'needs_reconciliation')),
    phase TEXT NOT NULL DEFAULT 'admission'
        CHECK (phase IN ('admission', 'decomposition', 'delegation', 'collaboration',
                         'synthesis', 'review', 'execution', 'verification', 'recovery', 'completed')),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    state_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(state_snapshot) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT director_missions_scope_contract CHECK (
        (scope_type = 'workspace' AND workspace_id IS NOT NULL AND admission_idempotency_key IS NOT NULL)
        OR
        (scope_type = 'platform' AND workspace_id IS NULL AND admission_idempotency_key IS NULL)
    ),
    CONSTRAINT director_missions_task_ids_array CHECK (jsonb_typeof(task_ids) = 'array'),
    CONSTRAINT director_missions_workspace_admission_fk
        FOREIGN KEY (workspace_id, admission_idempotency_key)
        REFERENCES public.director_mission_admissions(workspace_id, idempotency_key)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS director_missions_workspace_updated_idx
    ON public.director_missions (workspace_id, updated_at DESC)
    WHERE scope_type = 'workspace';

CREATE TABLE IF NOT EXISTS public.director_mission_events (
    event_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    mission_id TEXT NOT NULL REFERENCES public.director_missions(mission_id) ON DELETE RESTRICT,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('workspace', 'platform')),
    workspace_id TEXT,
    sequence_number BIGINT NOT NULL CHECK (sequence_number > 0),
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 120),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('director', 'engine', 'operator', 'system')),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    recipient_type TEXT CHECK (recipient_type IS NULL OR recipient_type IN ('director', 'engine', 'operator', 'system')),
    recipient_id TEXT,
    task_id TEXT,
    message_id TEXT,
    thread_id TEXT,
    round_number INTEGER NOT NULL DEFAULT 0 CHECK (round_number >= 0),
    correlation_id TEXT NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 128),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 160),
    request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence_refs) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT director_mission_events_scope_contract CHECK (
        (scope_type = 'workspace' AND workspace_id IS NOT NULL)
        OR
        (scope_type = 'platform' AND workspace_id IS NULL)
    ),
    CONSTRAINT director_mission_events_recipient_contract CHECK (
        (recipient_type IS NULL AND recipient_id IS NULL)
        OR
        (recipient_type IS NOT NULL AND recipient_id IS NOT NULL AND length(recipient_id) BETWEEN 1 AND 128)
    ),
    CONSTRAINT director_mission_events_mission_sequence_key UNIQUE (mission_id, sequence_number),
    CONSTRAINT director_mission_events_idempotency_key UNIQUE (mission_id, idempotency_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS director_mission_events_message_id_key
    ON public.director_mission_events (mission_id, message_id)
    WHERE message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS director_mission_events_mission_timeline_idx
    ON public.director_mission_events (mission_id, sequence_number ASC);
CREATE INDEX IF NOT EXISTS director_mission_events_workspace_timeline_idx
    ON public.director_mission_events (workspace_id, created_at DESC)
    WHERE scope_type = 'workspace';

ALTER TABLE public.director_missions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.director_mission_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.director_missions FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.director_mission_events FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.director_missions TO service_role;
GRANT SELECT ON TABLE public.director_mission_events TO service_role;

CREATE OR REPLACE FUNCTION public.reject_director_mission_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $
BEGIN
    RAISE EXCEPTION 'director_mission_events_append_only';
END;
$$;

DROP TRIGGER IF EXISTS director_mission_events_append_only_guard
    ON public.director_mission_events;
CREATE TRIGGER director_mission_events_append_only_guard
    BEFORE UPDATE OR DELETE ON public.director_mission_events
    FOR EACH ROW EXECUTE FUNCTION public.reject_director_mission_event_mutation();

CREATE OR REPLACE FUNCTION public.guard_director_mission_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $body$
BEGIN
    IF NEW.version <> 0 THEN
        RAISE EXCEPTION 'director_mission_must_start_at_version_zero';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS director_missions_initial_version_guard ON public.director_missions;
CREATE TRIGGER director_missions_initial_version_guard
    BEFORE INSERT ON public.director_missions
    FOR EACH ROW EXECUTE FUNCTION public.guard_director_mission_insert();

CREATE OR REPLACE FUNCTION public.guard_director_mission_state_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $body$
BEGIN
    IF NEW.mission_id <> OLD.mission_id
       OR NEW.scope_type <> OLD.scope_type
       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
       OR NEW.plan_id <> OLD.plan_id
       OR NEW.admission_idempotency_key IS DISTINCT FROM OLD.admission_idempotency_key
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'director_mission_identity_is_immutable';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'director_mission_state_requires_next_version';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.director_mission_events
         WHERE mission_id = NEW.mission_id
           AND sequence_number = NEW.version
    ) THEN
        RAISE EXCEPTION 'director_mission_state_requires_matching_event';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS director_missions_state_event_guard ON public.director_missions;
CREATE TRIGGER director_missions_state_event_guard
    BEFORE UPDATE ON public.director_missions
    FOR EACH ROW EXECUTE FUNCTION public.guard_director_mission_state_update();

CREATE OR REPLACE FUNCTION public.guard_director_mission_event_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $body$
DECLARE
    mission_row public.director_missions%ROWTYPE;
BEGIN
    SELECT * INTO mission_row
      FROM public.director_missions
     WHERE mission_id = NEW.mission_id;
    IF NOT FOUND
       OR mission_row.scope_type <> NEW.scope_type
       OR mission_row.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
        RAISE EXCEPTION 'director_mission_event_scope_mismatch';
    END IF;
    IF NEW.task_id IS NOT NULL AND NOT (mission_row.task_ids ? NEW.task_id) THEN
        RAISE EXCEPTION 'director_mission_event_task_scope_mismatch';
    END IF;
    IF NEW.event_type LIKE 'task.%' AND NEW.task_id IS NULL THEN
        RAISE EXCEPTION 'director_task_event_requires_task_id';
    END IF;
    IF NEW.sequence_number <> mission_row.version + 1 THEN
        RAISE EXCEPTION 'director_mission_event_sequence_mismatch';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS director_mission_events_scope_guard ON public.director_mission_events;
CREATE TRIGGER director_mission_events_scope_guard
    BEFORE INSERT ON public.director_mission_events
    FOR EACH ROW EXECUTE FUNCTION public.guard_director_mission_event_insert();

CREATE OR REPLACE FUNCTION public.append_director_mission_event(
    p_scope_type TEXT,
    p_workspace_id TEXT,
    p_mission_id TEXT,
    p_expected_version BIGINT,
    p_idempotency_key TEXT,
    p_request_fingerprint TEXT,
    p_event_type TEXT,
    p_actor_type TEXT,
    p_actor_id TEXT,
    p_recipient_type TEXT,
    p_recipient_id TEXT,
    p_task_id TEXT,
    p_message_id TEXT,
    p_thread_id TEXT,
    p_round_number INTEGER,
    p_correlation_id TEXT,
    p_payload JSONB,
    p_evidence_refs JSONB,
    p_status TEXT,
    p_phase TEXT,
    p_state_snapshot JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    mission_row public.director_missions%ROWTYPE;
    existing_event public.director_mission_events%ROWTYPE;
    next_status TEXT;
    next_phase TEXT;
    next_version BIGINT;
    inserted_event_id TEXT;
BEGIN
    IF p_scope_type NOT IN ('workspace', 'platform')
       OR (p_scope_type = 'workspace' AND (p_workspace_id IS NULL OR length(trim(p_workspace_id)) = 0))
       OR (p_scope_type = 'platform' AND p_workspace_id IS NOT NULL)
       OR p_mission_id IS NULL OR length(trim(p_mission_id)) = 0
       OR p_expected_version IS NULL OR p_expected_version < 0
       OR p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 160
       OR p_request_fingerprint IS NULL OR length(p_request_fingerprint) <> 64
       OR p_event_type IS NULL OR length(p_event_type) NOT BETWEEN 1 AND 120
       OR p_actor_type NOT IN ('director', 'engine', 'operator', 'system')
       OR p_actor_id IS NULL OR length(p_actor_id) NOT BETWEEN 1 AND 128
       OR (p_recipient_type IS NULL AND p_recipient_id IS NOT NULL)
       OR (p_recipient_type IS NOT NULL AND
           (p_recipient_type NOT IN ('director', 'engine', 'operator', 'system')
            OR p_recipient_id IS NULL OR length(p_recipient_id) NOT BETWEEN 1 AND 128))
       OR (p_task_id IS NOT NULL AND length(p_task_id) > 128)
       OR (p_message_id IS NOT NULL AND length(p_message_id) NOT BETWEEN 1 AND 128)
       OR (p_thread_id IS NOT NULL AND length(p_thread_id) NOT BETWEEN 1 AND 128)
       OR p_round_number IS NULL OR p_round_number < 0
       OR p_correlation_id IS NULL OR length(p_correlation_id) NOT BETWEEN 1 AND 128
       OR p_payload IS NULL OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR p_evidence_refs IS NULL OR jsonb_typeof(p_evidence_refs) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_evidence_refs) > 100
       OR p_state_snapshot IS NULL OR jsonb_typeof(p_state_snapshot) IS DISTINCT FROM 'object'
       OR octet_length(p_payload::text) > 65536
       OR octet_length(p_state_snapshot::text) > 32768 THEN
        RAISE EXCEPTION 'director_mission_event_contract_invalid';
    END IF;

    SELECT * INTO mission_row
      FROM public.director_missions
     WHERE mission_id = p_mission_id
       AND scope_type = p_scope_type
       AND workspace_id IS NOT DISTINCT FROM p_workspace_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'director_mission_scope_mismatch');
    END IF;

    SELECT * INTO existing_event
      FROM public.director_mission_events
     WHERE mission_id = p_mission_id
       AND idempotency_key = p_idempotency_key;

    IF FOUND THEN
        IF existing_event.request_fingerprint <> p_request_fingerprint THEN
            RETURN jsonb_build_object('error', 'director_mission_event_idempotency_conflict');
        END IF;
        RETURN jsonb_build_object(
            'replayed', TRUE,
            'event_id', existing_event.event_id,
            'sequence_number', existing_event.sequence_number,
            'version', mission_row.version,
            'status', mission_row.status,
            'phase', mission_row.phase
        );
    END IF;

    IF mission_row.version <> p_expected_version THEN
        RETURN jsonb_build_object(
            'error', 'director_mission_version_conflict',
            'expected_version', p_expected_version,
            'current_version', mission_row.version
        );
    END IF;

    next_status := COALESCE(p_status, mission_row.status);
    next_phase := COALESCE(p_phase, mission_row.phase);

    IF next_status NOT IN ('admitted', 'queued', 'running', 'awaiting_review', 'paused',
                           'completed', 'partial', 'failed', 'cancelled', 'needs_reconciliation')
       OR next_phase NOT IN ('admission', 'decomposition', 'delegation', 'collaboration',
                             'synthesis', 'review', 'execution', 'verification', 'recovery', 'completed') THEN
        RAISE EXCEPTION 'director_mission_state_value_invalid';
    END IF;

    IF next_status <> mission_row.status AND NOT (
        (mission_row.status = 'admitted' AND next_status IN ('queued', 'running', 'awaiting_review', 'paused', 'failed', 'cancelled', 'needs_reconciliation'))
        OR (mission_row.status = 'queued' AND next_status IN ('running', 'paused', 'failed', 'cancelled', 'needs_reconciliation'))
        OR (mission_row.status = 'running' AND next_status IN ('awaiting_review', 'paused', 'completed', 'partial', 'failed', 'cancelled', 'needs_reconciliation'))
        OR (mission_row.status = 'awaiting_review' AND next_status IN ('queued', 'running', 'paused', 'completed', 'partial', 'failed', 'cancelled', 'needs_reconciliation'))
        OR (mission_row.status = 'paused' AND next_status IN ('queued', 'running', 'failed', 'cancelled', 'needs_reconciliation'))
        OR (mission_row.status = 'needs_reconciliation' AND next_status IN ('queued', 'running', 'paused', 'completed', 'partial', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'director_mission_status_transition_invalid:%->%', mission_row.status, next_status;
    END IF;

    next_version := mission_row.version + 1;

    INSERT INTO public.director_mission_events (
        mission_id, scope_type, workspace_id, sequence_number, event_type,
        actor_type, actor_id, recipient_type, recipient_id, task_id,
        message_id, thread_id, round_number, correlation_id, idempotency_key,
        request_fingerprint, payload, evidence_refs
    ) VALUES (
        p_mission_id, p_scope_type, p_workspace_id, next_version, p_event_type,
        p_actor_type, p_actor_id, p_recipient_type, p_recipient_id, p_task_id,
        p_message_id, p_thread_id, p_round_number, p_correlation_id, p_idempotency_key,
        p_request_fingerprint, p_payload, p_evidence_refs
    ) RETURNING event_id INTO inserted_event_id;

    UPDATE public.director_missions
       SET status = next_status,
           phase = next_phase,
           version = next_version,
           state_snapshot = p_state_snapshot,
           updated_at = NOW()
     WHERE mission_id = p_mission_id;

    RETURN jsonb_build_object(
        'replayed', FALSE,
        'event_id', inserted_event_id,
        'sequence_number', next_version,
        'version', next_version,
        'status', next_status,
        'phase', next_phase
    );
END;
$$;

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
AS $$
DECLARE
    admission_result JSONB;
    mission_row public.director_missions%ROWTYPE;
    event_result JSONB;
    initial_state JSONB;
BEGIN
    admission_result := public.stage_director_mission(
        p_workspace_id, p_idempotency_key, p_plan_id,
        p_mission_id, p_request_fingerprint, p_tasks
    );

    IF admission_result ? 'error' THEN
        RETURN admission_result;
    END IF;

    initial_state := jsonb_build_object(
        'task_ids', admission_result->'task_ids',
        'active_reasoning_round', 0,
        'unresolved_disagreements', '[]'::jsonb,
        'selected_engines', '[]'::jsonb
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
        jsonb_build_object('plan_id', p_plan_id, 'task_ids', admission_result->'task_ids'),
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
$$;

-- Backfill existing workspace admission receipts, if any, without touching task rows.
INSERT INTO public.director_missions (
    mission_id, scope_type, workspace_id, plan_id, admission_idempotency_key,
    task_ids, status, phase, version, state_snapshot, created_at, updated_at
)
SELECT
    a.mission_id, 'workspace', a.workspace_id, a.plan_id, a.idempotency_key,
    a.task_ids, 'admitted', 'admission', 0,
    jsonb_build_object(
        'task_ids', a.task_ids,
        'active_reasoning_round', 0,
        'unresolved_disagreements', '[]'::jsonb,
        'selected_engines', '[]'::jsonb,
        'legacy_backfill', TRUE
    ),
    a.created_at, a.created_at
FROM public.director_mission_admissions a
ON CONFLICT (mission_id) DO NOTHING;

DO $$
DECLARE
    mission_row RECORD;
    event_result JSONB;
    initial_state JSONB;
BEGIN
    FOR mission_row IN
        SELECT m.*, a.request_fingerprint
          FROM public.director_missions m
          JOIN public.director_mission_admissions a
            ON a.workspace_id = m.workspace_id
           AND a.idempotency_key = m.admission_idempotency_key
         WHERE m.scope_type = 'workspace'
           AND m.version = 0
    LOOP
        initial_state := mission_row.state_snapshot;
        event_result := public.append_director_mission_event(
            'workspace',
            mission_row.workspace_id,
            mission_row.mission_id,
            0,
            'mission-admission:' || mission_row.admission_idempotency_key,
            mission_row.request_fingerprint,
            'mission.admitted',
            'system',
            'migration-176',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            0,
            mission_row.mission_id,
            jsonb_build_object('plan_id', mission_row.plan_id, 'task_ids', mission_row.task_ids, 'backfilled', TRUE),
            '[]'::jsonb,
            'admitted',
            'admission',
            initial_state
        );
        IF event_result ? 'error' THEN
            RAISE EXCEPTION 'director_mission_backfill_event_failed:%', event_result->>'error';
        END IF;
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) FROM anon;
REVOKE ALL ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) TO service_role;

REVOKE ALL ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon;
REVOKE ALL ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

COMMENT ON TABLE public.director_missions IS
    'Versioned Director mission aggregate; director_tasks remains the canonical task record.';
COMMENT ON TABLE public.director_mission_events IS
    'Append-only, workspace-scoped Director collaboration, evidence, handoff, and lifecycle journal.';
COMMENT ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) IS
    'Atomically appends a mission event and advances the mission snapshot/version using optimistic concurrency.';
COMMENT ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) IS
    'Backward-compatible workspace mission admission wrapper that also creates the durable mission aggregate and initial event.';
