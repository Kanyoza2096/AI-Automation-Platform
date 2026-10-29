create table if not exists it_service_remediations (
  id text primary key,
  workspace_id text not null,
  service_id text not null,
  provider text not null default 'github',
  owner text not null,
  repo text not null,
  workflow_id text not null,
  ref text not null,
  reason text not null,
  status text not null default 'requested',
  provider_request_id text,
  created_at timestamptz not null default now()
);
create index if not exists idx_it_service_remediations_ws_status on it_service_remediations(workspace_id, status, created_at);

create table if not exists it_service_remediation_reconciliation_evidence (
  id text primary key,
  workspace_id text not null,
  remediation_id text not null,
  service_id text not null,
  provider text not null,
  owner text not null,
  repo text not null,
  workflow_id text not null,
  ref text not null,
  attempt_number integer not null,
  status text not null,
  terminal boolean not null default false,
  observed jsonb not null default '{}'::jsonb,
  verification_state text not null,
  evidence jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null default now()
);
create index if not exists idx_it_service_remediation_recon_ws_id on it_service_remediation_reconciliation_evidence(workspace_id, remediation_id, attempt_number desc);
