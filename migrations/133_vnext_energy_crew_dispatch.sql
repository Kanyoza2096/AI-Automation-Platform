create table if not exists energy_crew_dispatch_provider_bindings (
    id uuid primary key default gen_random_uuid(), workspace_id uuid not null,
    provider text not null, status text not null default 'active', metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists idx_energy_crew_dispatch_bindings_ws_provider on energy_crew_dispatch_provider_bindings(workspace_id, provider, status);

create table if not exists energy_crew_dispatches (
    id uuid primary key default gen_random_uuid(), workspace_id uuid not null, work_order_id uuid not null,
    crew_id text not null, provider text not null default 'jira', project_key text not null, issue_type text not null,
    status text not null default 'requested', provider_request_id text, provider_issue_key text,
    created_at timestamptz not null default now()
);
create index if not exists idx_energy_crew_dispatches_ws_status on energy_crew_dispatches(workspace_id, status);

create table if not exists energy_crew_dispatch_reconciliation_evidence (
    id uuid primary key default gen_random_uuid(), workspace_id uuid not null, dispatch_id uuid not null,
    attempt_number integer not null, status text not null, terminal boolean not null default false,
    observed jsonb not null default '{}'::jsonb, verification_state text not null, evidence jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now()
);
create index if not exists idx_energy_crew_dispatch_recon_ws_dispatch on energy_crew_dispatch_reconciliation_evidence(workspace_id, dispatch_id, attempt_number desc);

create table if not exists energy_crew_dispatch_outcomes (
    id uuid primary key default gen_random_uuid(), workspace_id uuid not null, dispatch_id uuid not null,
    status text not null default 'recorded', evidence_ref text not null default '', recorded_at timestamptz not null default now()
);
