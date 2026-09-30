-- V68.1 — Business autonomy envelope persistence.
-- Declarative contract only. This table MUST NOT be treated as execution authority.
create table if not exists business_autonomy_envelopes (
    envelope_id text primary key,
    workspace_id uuid not null,
    objective text not null,
    version integer not null default 1 check (version >= 1),
    mode text not null check (mode in ('observe','suggest','execute')),
    state text not null check (state in ('draft','active','expired','suspended','revoked')),
    goal_id text,
    scope jsonb not null default '{}'::jsonb,
    success_metrics jsonb not null,
    allowed_actions jsonb not null default '[]'::jsonb,
    risk_boundary jsonb not null default '{}'::jsonb,
    resource_limits jsonb not null default '[]'::jsonb,
    escalation_triggers jsonb not null default '[]'::jsonb,
    valid_from timestamptz not null,
    valid_until timestamptz,
    authority text not null default 'none' check (authority = 'none'),
    execution_effect text not null default 'none' check (execution_effect = 'none'),
    approval_effect text not null default 'none' check (approval_effect = 'none'),
    policy_effect text not null default 'none' check (policy_effect = 'none'),
    fingerprint text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, envelope_id, version),
    check (valid_until is null or valid_until > valid_from)
);

create index if not exists idx_business_autonomy_envelopes_workspace_state
    on business_autonomy_envelopes (workspace_id, state);

alter table business_autonomy_envelopes enable row level security;

-- No permissive RLS policy is installed here. Service/control-plane paths
-- use the existing privileged persistence boundary; ordinary sessions remain
-- fail-closed until a workspace-membership policy is explicitly wired.
