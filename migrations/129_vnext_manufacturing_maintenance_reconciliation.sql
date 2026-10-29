create table if not exists manufacturing_maintenance_provider_bindings (
  id text primary key,
  workspace_id text not null,
  provider text not null default 'jira',
  status text not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_mfg_maintenance_bindings_ws_provider on manufacturing_maintenance_provider_bindings(workspace_id, provider, status);

create table if not exists manufacturing_maintenance_requests (
  id text primary key,
  workspace_id text not null,
  maintenance_id text not null,
  provider text not null default 'jira',
  project_key text not null,
  issue_type text not null,
  reason text not null,
  status text not null default 'requested',
  provider_request_id text,
  provider_issue_key text,
  created_at timestamptz not null default now()
);
create index if not exists idx_mfg_maintenance_requests_ws_status on manufacturing_maintenance_requests(workspace_id, status, created_at);

create table if not exists manufacturing_maintenance_reconciliation_evidence (
  id text primary key,
  workspace_id text not null,
  request_id text not null,
  attempt_number integer not null,
  status text not null,
  terminal boolean not null default false,
  observed jsonb not null default '{}'::jsonb,
  verification_state text not null,
  evidence jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null default now()
);
create index if not exists idx_mfg_maintenance_recon_ws_id on manufacturing_maintenance_reconciliation_evidence(workspace_id, request_id, attempt_number desc);
