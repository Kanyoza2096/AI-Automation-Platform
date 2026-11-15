-- 171: Workspace-scoped governed plan proposals sourced only from completed evaluations.
-- Approval changes proposal state only; it never creates executable tasks or grants runtime authority.

CREATE TABLE IF NOT EXISTS public.director_workspace_plans (
    plan_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_evaluation_id TEXT NOT NULL
        REFERENCES public.director_website_evaluations(evaluation_id),
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected')),
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    approved_by TEXT,
    approval_reason TEXT,
    approved_at TIMESTAMPTZ,
    plan JSONB NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, source_evaluation_id),
    CHECK (
        (approval_status = 'pending' AND approved_by IS NULL AND approved_at IS NULL)
        OR (approval_status IN ('approved', 'rejected')
            AND approved_by IS NOT NULL AND approved_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_director_workspace_plans_workspace_created
    ON public.director_workspace_plans (workspace_id, created_at DESC);

ALTER TABLE public.director_workspace_plans ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.director_workspace_plans FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.director_workspace_plans TO service_role;

CREATE OR REPLACE FUNCTION public.create_director_workspace_plan(
    p_plan_id TEXT,
    p_workspace_id TEXT,
    p_source_evaluation_id TEXT,
    p_requested_by TEXT,
    p_plan JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    source_row public.director_website_evaluations%ROWTYPE;
    existing_row public.director_workspace_plans%ROWTYPE;
BEGIN
    IF COALESCE(p_plan_id, '') = ''
       OR COALESCE(p_workspace_id, '') = ''
       OR COALESCE(p_source_evaluation_id, '') = ''
       OR COALESCE(p_requested_by, '') = ''
       OR p_plan IS NULL
       OR jsonb_typeof(p_plan) IS DISTINCT FROM 'object'
       OR p_plan->>'plan_id' IS DISTINCT FROM p_plan_id
       OR p_plan->>'workspace_id' IS DISTINCT FROM p_workspace_id
       OR p_plan->>'status' IS DISTINCT FROM 'proposed'
       OR p_plan->>'approval_status' IS DISTINCT FROM 'pending'
       OR p_plan->>'approval_required' IS DISTINCT FROM 'true'
       OR p_plan->>'requested_by' IS DISTINCT FROM p_requested_by
       OR p_plan->>'authority' IS DISTINCT FROM 'none'
       OR p_plan->>'advisory_only' IS DISTINCT FROM 'true'
       OR p_plan->>'tasks_created' IS DISTINCT FROM 'false'
       OR p_plan->>'content_generation_started' IS DISTINCT FROM 'false'
       OR p_plan->>'public_publishing_authorized' IS DISTINCT FROM 'false'
       OR p_plan->>'deployment_authorized' IS DISTINCT FROM 'false'
       OR jsonb_typeof(p_plan->'steps') IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_plan->'steps') NOT BETWEEN 1 AND 12 THEN
        RAISE EXCEPTION 'director_workspace_plan_contract_invalid';
    END IF;

    SELECT * INTO source_row
      FROM public.director_website_evaluations
     WHERE evaluation_id = p_source_evaluation_id
       AND workspace_id = p_workspace_id
       AND status = 'evaluated'
     FOR SHARE;

    IF NOT FOUND
       OR source_row.payload->>'action_warranted' IS DISTINCT FROM 'true'
       OR source_row.payload->>'authority' IS DISTINCT FROM 'none'
       OR source_row.payload->>'advisory_only' IS DISTINCT FROM 'true'
       OR p_plan->'source'->>'evaluation_id' IS DISTINCT FROM source_row.evaluation_id
       OR p_plan->'source'->>'integration_id' IS DISTINCT FROM source_row.integration_id
       OR p_plan->'source'->>'event_id' IS DISTINCT FROM source_row.event_id
       OR p_plan->'source'->>'context_hash' IS DISTINCT FROM source_row.context_hash THEN
        RAISE EXCEPTION 'director_workspace_plan_source_invalid';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_workspace_id || ':' || p_source_evaluation_id, 0));

    INSERT INTO public.director_workspace_plans (
        plan_id, workspace_id, source_evaluation_id, requested_by, plan
    ) VALUES (
        p_plan_id, p_workspace_id, p_source_evaluation_id, p_requested_by, p_plan
    ) ON CONFLICT (workspace_id, source_evaluation_id) DO NOTHING;

    SELECT * INTO existing_row
      FROM public.director_workspace_plans
     WHERE workspace_id = p_workspace_id
       AND source_evaluation_id = p_source_evaluation_id
     FOR UPDATE;

    RETURN existing_row.plan_id = p_plan_id
       AND existing_row.requested_by = p_requested_by
       AND existing_row.plan = p_plan;
END;
$$;

CREATE OR REPLACE FUNCTION public.decide_director_workspace_plan(
    p_plan_id TEXT,
    p_workspace_id TEXT,
    p_actor_id TEXT,
    p_decision TEXT,
    p_reason TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    current_row public.director_workspace_plans%ROWTYPE;
    next_status TEXT;
BEGIN
    IF COALESCE(p_plan_id, '') = ''
       OR COALESCE(p_workspace_id, '') = ''
       OR COALESCE(p_actor_id, '') = ''
       OR p_decision IS NULL
       OR p_decision NOT IN ('approve', 'reject') THEN
        RAISE EXCEPTION 'director_workspace_plan_decision_invalid';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_workspace_id || ':' || p_plan_id, 0));
    SELECT * INTO current_row
      FROM public.director_workspace_plans
     WHERE plan_id = p_plan_id AND workspace_id = p_workspace_id
     FOR UPDATE;

    IF NOT FOUND OR current_row.requested_by = p_actor_id THEN
        RAISE EXCEPTION 'director_workspace_plan_independent_approval_required';
    END IF;

    next_status := CASE WHEN p_decision = 'approve' THEN 'approved' ELSE 'rejected' END;

    IF current_row.approval_status <> 'pending' THEN
        RETURN current_row.approval_status = next_status
           AND current_row.approved_by = p_actor_id;
    END IF;

    UPDATE public.director_workspace_plans
       SET status = next_status,
           approval_status = next_status,
           approved_by = p_actor_id,
           approval_reason = left(COALESCE(p_reason, ''), 1000),
           approved_at = now(),
           plan = jsonb_set(
               jsonb_set(plan, '{status}', to_jsonb(next_status), true),
               '{approval_status}', to_jsonb(next_status), true
           ),
           updated_at = now()
     WHERE plan_id = p_plan_id AND workspace_id = p_workspace_id;

    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.create_director_workspace_plan(TEXT, TEXT, TEXT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decide_director_workspace_plan(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_director_workspace_plan(TEXT, TEXT, TEXT, TEXT, JSONB)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.decide_director_workspace_plan(TEXT, TEXT, TEXT, TEXT, TEXT)
    TO service_role;

COMMENT ON TABLE public.director_workspace_plans IS
    'Workspace-scoped advisory plan proposals. Independent approval changes proposal state only and does not authorize execution.';
