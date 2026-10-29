create table if not exists government_field_service_provider_bindings (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    provider text not null,
    status text not null default 'active',
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists idx_gov_field_service_bindings_ws_provider on government_field_service_provider_bindings(workspace_id, provider, status);

create table if not exists government_field_service_dispatches (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    request_id uuid not null,
    team_id text not null,
    provider text not null default 'jira',
    project_key text not null,
    issue_type text not null,
    status text not null default 'requested',
    provider_request_id text,
    provider_issue_key text,
    created_at timestamptz not null default now()
);
create index if not exists idx_gov_field_service_dispatches_ws_status on government_field_service_dispatches(workspace_id, status);

create table if not exists government_field_service_reconciliation_evidence (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    dispatch_id uuid not null,
    attempt_number integer not null,
    status text not null,
    terminal boolean not null default false,
    observed jsonb not null default '{}'::jsonb,
    verification_state text not null,
    evidence jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now()
);
create index if not exists idx_gov_field_service_recon_ws_dispatch on government_field_service_reconciliation_evidence(workspace_id, dispatch_id, attempt_number desc);
