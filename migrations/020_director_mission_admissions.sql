-- Durable, workspace-scoped and idempotent admission of Director missions.
-- The RPC is the only write path for a mission admission: its task rows and
-- idempotency receipt commit or roll back together.

CREATE TABLE IF NOT EXISTS director_mission_admissions (
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    task_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, idempotency_key)
);

ALTER TABLE director_mission_admissions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION stage_director_mission(
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
    existing director_mission_admissions%ROWTYPE;
    task_row JSONB;
    ids JSONB := '[]'::jsonb;
    inserted_count INTEGER := 0;
BEGIN
    IF p_workspace_id IS NULL OR length(trim(p_workspace_id)) = 0
       OR p_idempotency_key IS NULL OR length(trim(p_idempotency_key)) = 0
       OR p_plan_id IS NULL OR length(trim(p_plan_id)) = 0
       OR p_mission_id IS NULL OR length(trim(p_mission_id)) = 0
       OR p_request_fingerprint IS NULL OR length(trim(p_request_fingerprint)) = 0
       OR jsonb_typeof(p_tasks) <> 'array'
       OR jsonb_array_length(p_tasks) < 1 OR jsonb_array_length(p_tasks) > 12 THEN
        RAISE EXCEPTION 'mission_admission_input_invalid';
    END IF;

    INSERT INTO director_mission_admissions (
        workspace_id, idempotency_key, plan_id, mission_id,
        request_fingerprint, task_ids
    ) VALUES (
        p_workspace_id, p_idempotency_key, p_plan_id, p_mission_id,
        p_request_fingerprint, '[]'::jsonb
    ) ON CONFLICT (workspace_id, idempotency_key) DO NOTHING;

    IF NOT FOUND THEN
        SELECT * INTO existing
          FROM director_mission_admissions
         WHERE workspace_id = p_workspace_id
           AND idempotency_key = p_idempotency_key
         FOR UPDATE;
        IF existing.request_fingerprint <> p_request_fingerprint
           OR existing.plan_id <> p_plan_id THEN
            RETURN jsonb_build_object('error', 'mission_idempotency_conflict');
        END IF;
        RETURN jsonb_build_object(
            'mission_id', existing.mission_id,
            'task_ids', existing.task_ids,
            'replayed', TRUE
        );
    END IF;

    FOR task_row IN SELECT value FROM jsonb_array_elements(p_tasks)
    LOOP
        IF task_row->>'workspace_id' <> p_workspace_id
           OR task_row->>'status' <> 'approval_required'
           OR jsonb_typeof(task_row->'payload') <> 'object'
           OR COALESCE(task_row->>'id', '') = ''
           OR task_row->'payload'->>'id' <> task_row->>'id'
           OR task_row->'payload'->>'workspace_id' <> p_workspace_id
           OR task_row->'payload'->>'status' <> 'approval_required'
           OR task_row->'payload'->>'mission_id' <> p_mission_id THEN
            RAISE EXCEPTION 'mission_task_contract_invalid';
        END IF;

        INSERT INTO director_tasks (id, workspace_id, status, payload, created_at, updated_at)
        VALUES (
            task_row->>'id',
            p_workspace_id,
            'approval_required',
            task_row->'payload',
            COALESCE((task_row->>'created_at')::timestamptz, NOW()),
            NOW()
        );
        ids := ids || jsonb_build_array(task_row->>'id');
        inserted_count := inserted_count + 1;
    END LOOP;

    IF inserted_count <> jsonb_array_length(p_tasks) THEN
        RAISE EXCEPTION 'mission_task_count_mismatch';
    END IF;

    UPDATE director_mission_admissions
       SET task_ids = ids
     WHERE workspace_id = p_workspace_id
       AND idempotency_key = p_idempotency_key;

    RETURN jsonb_build_object(
        'mission_id', p_mission_id,
        'task_ids', ids,
        'replayed', FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon;
REVOKE ALL ON FUNCTION stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;
