-- Repair Director mission admission for workspace-scoped task rows.
-- The prior RPC omitted scope_type. After migration 170, director_tasks
-- requires workspace_id != NULL iff scope_type = 'workspace'. The default
-- scope_type is 'platform', so the old RPC violates the ownership constraint.
-- This migration is additive: it preserves all existing task/plan rows.

CREATE TABLE IF NOT EXISTS public.director_mission_admissions (
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    task_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT director_mission_admissions_pkey
        PRIMARY KEY (workspace_id, idempotency_key),
    CONSTRAINT director_mission_admissions_key_length
        CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    CONSTRAINT director_mission_admissions_task_ids_array
        CHECK (jsonb_typeof(task_ids) = 'array')
);

-- The admission table already exists in some production environments. CREATE
-- TABLE IF NOT EXISTS does not retrofit constraints onto an existing table.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.director_mission_admissions'::regclass
           AND conname = 'director_mission_admissions_key_length'
    ) THEN
        ALTER TABLE public.director_mission_admissions
            ADD CONSTRAINT director_mission_admissions_key_length
            CHECK (length(idempotency_key) BETWEEN 1 AND 128);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.director_mission_admissions'::regclass
           AND conname = 'director_mission_admissions_task_ids_array'
    ) THEN
        ALTER TABLE public.director_mission_admissions
            ADD CONSTRAINT director_mission_admissions_task_ids_array
            CHECK (jsonb_typeof(task_ids) = 'array');
    END IF;
END;
$$;

ALTER TABLE public.director_mission_admissions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_mission_admissions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.director_mission_admissions TO service_role;

CREATE OR REPLACE FUNCTION public.stage_director_mission(
    p_workspace_id TEXT,
    p_idempotency_key TEXT,
    p_plan_id TEXT,
    p_mission_id TEXT,
    p_request_fingerprint TEXT,
    p_tasks JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    existing public.director_mission_admissions%ROWTYPE;
    task_row JSONB;
    ids JSONB := '[]'::jsonb;
    inserted_count INTEGER := 0;
    supplied_count INTEGER := 0;
    unique_task_count INTEGER := 0;
BEGIN
    IF p_workspace_id IS NULL OR length(trim(p_workspace_id)) = 0
       OR p_idempotency_key IS NULL OR length(trim(p_idempotency_key)) NOT BETWEEN 1 AND 128
       OR p_plan_id IS NULL OR length(trim(p_plan_id)) = 0
       OR p_mission_id IS NULL OR length(trim(p_mission_id)) = 0
       OR p_request_fingerprint IS NULL OR length(trim(p_request_fingerprint)) <> 64
       OR p_tasks IS NULL THEN
        RAISE EXCEPTION 'mission_admission_input_invalid';
    END IF;

    IF jsonb_typeof(p_tasks) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'mission_admission_tasks_must_be_array';
    END IF;

    supplied_count := jsonb_array_length(p_tasks);
    IF supplied_count < 1 OR supplied_count > 12 THEN
        RAISE EXCEPTION 'mission_admission_task_count_invalid';
    END IF;

    SELECT count(DISTINCT value->>'id')
      INTO unique_task_count
      FROM jsonb_array_elements(p_tasks);
    IF unique_task_count <> supplied_count THEN
        RAISE EXCEPTION 'mission_admission_task_ids_must_be_unique_and_nonempty';
    END IF;

    INSERT INTO public.director_mission_admissions (
        workspace_id, idempotency_key, plan_id, mission_id,
        request_fingerprint, task_ids
    ) VALUES (
        p_workspace_id, p_idempotency_key, p_plan_id, p_mission_id,
        p_request_fingerprint, '[]'::jsonb
    ) ON CONFLICT (workspace_id, idempotency_key) DO NOTHING;

    IF NOT FOUND THEN
        SELECT * INTO existing
          FROM public.director_mission_admissions
         WHERE workspace_id = p_workspace_id
           AND idempotency_key = p_idempotency_key
         FOR UPDATE;

        IF existing.request_fingerprint <> p_request_fingerprint
           OR existing.plan_id <> p_plan_id
           OR existing.mission_id <> p_mission_id THEN
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
        IF task_row->>'workspace_id' IS DISTINCT FROM p_workspace_id
           OR task_row->>'status' IS DISTINCT FROM 'approval_required'
           OR jsonb_typeof(task_row->'payload') IS DISTINCT FROM 'object'
           OR COALESCE(task_row->>'id', '') = ''
           OR task_row->'payload'->>'id' IS DISTINCT FROM task_row->>'id'
           OR task_row->'payload'->>'workspace_id' IS DISTINCT FROM p_workspace_id
           OR task_row->'payload'->>'scope_type' IS DISTINCT FROM 'workspace'
           OR task_row->'payload'->>'status' IS DISTINCT FROM 'approval_required'
           OR task_row->'payload'->>'mission_id' IS DISTINCT FROM p_mission_id THEN
            RAISE EXCEPTION 'mission_task_contract_invalid';
        END IF;

        INSERT INTO public.director_tasks (
            id, workspace_id, scope_type, status, payload, created_at
        ) VALUES (
            task_row->>'id',
            p_workspace_id,
            'workspace',
            'approval_required',
            task_row->'payload',
            COALESCE((task_row->>'created_at')::timestamptz, NOW())
        );

        ids := ids || jsonb_build_array(task_row->>'id');
        inserted_count := inserted_count + 1;
    END LOOP;

    IF inserted_count <> supplied_count THEN
        RAISE EXCEPTION 'mission_task_count_mismatch';
    END IF;

    UPDATE public.director_mission_admissions
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

REVOKE ALL ON FUNCTION public.stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM anon;
REVOKE ALL ON FUNCTION public.stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

COMMENT ON FUNCTION public.stage_director_mission(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) IS
    'Atomically admits workspace-scoped Director tasks as approval_required; explicitly sets scope_type=workspace and grants no execution authority.';
