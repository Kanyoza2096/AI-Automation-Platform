-- V70.1 — Real Business Operating Layer
-- Descriptive/control-plane state only. It cannot grant execution authority.

create table if not exists business_operating_profiles (
    profile_id text primary key,
    workspace_id uuid not null,
    name text not null,
    industry text not null default '',
    description text not null default '',
    operating_hours jsonb not null default '{}'::jsonb,
    policies jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    state text not null default 'draft',
    version integer not null default 1,
    authority_granted boolean not null default false,
    execution_effect text not null default 'none',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint bop_no_authority check (authority_granted = false),
    constraint bop_no_execution check (execution_effect = 'none'),
    constraint bop_state check (state in ('draft','ready','active','degraded','suspended'))
);

create index if not exists idx_bop_workspace on business_operating_profiles(workspace_id);

create table if not exists business_operating_objectives (
    objective_id text primary key,
    workspace_id uuid not null,
    profile_id text not null references business_operating_profiles(profile_id),
    objective text not null,
    envelope_id text not null,
    workflow_ids jsonb not null default '[]'::jsonb,
    state text not null default 'draft',
    authority_granted boolean not null default false,
    execution_effect text not null default 'none',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint boo_no_authority check (authority_granted = false),
    constraint boo_no_execution check (execution_effect = 'none'),
    constraint boo_state check (state in ('draft','active','paused','completed','blocked'))
);

create index if not exists idx_boo_workspace on business_operating_objectives(workspace_id);
create index if not exists idx_boo_profile on business_operating_objectives(profile_id);

alter table business_operating_profiles enable row level security;
alter table business_operating_objectives enable row level security;

-- Workspace members may read their own operating state; server-side governed
-- services own writes. No client policy is granted here for authority fields.
create policy business_operating_profiles_member_select
on business_operating_profiles for select
using (workspace_id = auth.uid()::uuid or workspace_id in (
    select workspace_id from workspace_members where user_id = auth.uid()
));

create policy business_operating_objectives_member_select
on business_operating_objectives for select
using (workspace_id = auth.uid()::uuid or workspace_id in (
    select workspace_id from workspace_members where user_id = auth.uid()
));
