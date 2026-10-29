-- Marketing & Media sector completion boundary.
-- Domain state only; external publishing remains Action Kernel -> connector.
create table if not exists marketing_campaigns (
    id text primary key,
    workspace_id text not null,
    name text not null,
    objective_id text not null,
    status text not null default 'draft',
    start_at timestamptz,
    end_at timestamptz,
    budget numeric(18,2) not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz,
    check (status in ('draft','active','paused','completed','cancelled')),
    check (budget >= 0)
);
create index if not exists idx_marketing_campaigns_workspace on marketing_campaigns(workspace_id);

create table if not exists marketing_content_schedules (
    id text primary key,
    workspace_id text not null,
    campaign_id text not null references marketing_campaigns(id),
    candidate_id text not null,
    platform text not null,
    scheduled_at timestamptz not null,
    status text not null default 'scheduled',
    decision_id text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz
);
create index if not exists idx_marketing_content_schedules_workspace on marketing_content_schedules(workspace_id);
create index if not exists idx_marketing_content_schedules_campaign on marketing_content_schedules(campaign_id);

create table if not exists marketing_outcomes (
    id text primary key,
    workspace_id text not null,
    campaign_id text not null references marketing_campaigns(id),
    candidate_id text,
    metric text not null,
    value numeric(20,6) not null,
    observed_at timestamptz not null default now(),
    source text not null default 'analytics',
    evidence jsonb not null default '{}'::jsonb
);
create index if not exists idx_marketing_outcomes_workspace on marketing_outcomes(workspace_id);
create index if not exists idx_marketing_outcomes_campaign on marketing_outcomes(campaign_id);
