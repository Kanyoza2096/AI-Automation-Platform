-- V70.2 Customer & Sales Operating Loop
-- State/evidence only. No authority and no provider execution is stored here.

CREATE TABLE IF NOT EXISTS public.v70_sales_lead_state (
    workspace_id uuid NOT NULL,
    lead_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('new','qualified','follow_up_due','engaged','won','lost','opted_out')),
    name text NOT NULL DEFAULT '',
    email text NOT NULL DEFAULT '',
    phone text NOT NULL DEFAULT '',
    company text NOT NULL DEFAULT '',
    amount numeric NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    last_event_id text NOT NULL,
    updated_by_event text NOT NULL,
    authority_granted boolean NOT NULL DEFAULT false CHECK (authority_granted = false),
    execution_effect text NOT NULL DEFAULT 'none' CHECK (execution_effect = 'none'),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, lead_id)
);

CREATE TABLE IF NOT EXISTS public.v70_sales_lead_event_fence (
    workspace_id uuid NOT NULL,
    event_id text NOT NULL,
    lead_id text NOT NULL,
    event_name text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, event_id),
    authority_granted boolean NOT NULL DEFAULT false CHECK (authority_granted = false),
    execution_effect text NOT NULL DEFAULT 'none' CHECK (execution_effect = 'none')
);

CREATE INDEX IF NOT EXISTS idx_v70_sales_lead_state_workspace_state
    ON public.v70_sales_lead_state(workspace_id, state);
CREATE INDEX IF NOT EXISTS idx_v70_sales_lead_events_workspace_lead
    ON public.v70_sales_lead_event_fence(workspace_id, lead_id, occurred_at DESC);

ALTER TABLE public.v70_sales_lead_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.v70_sales_lead_event_fence ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS v70_sales_lead_state_member_select ON public.v70_sales_lead_state;
CREATE POLICY v70_sales_lead_state_member_select ON public.v70_sales_lead_state
    FOR SELECT USING (public.is_workspace_member(workspace_id));

DROP POLICY IF EXISTS v70_sales_lead_event_member_select ON public.v70_sales_lead_event_fence;
CREATE POLICY v70_sales_lead_event_member_select ON public.v70_sales_lead_event_fence
    FOR SELECT USING (public.is_workspace_member(workspace_id));
