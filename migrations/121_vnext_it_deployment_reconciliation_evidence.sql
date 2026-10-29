-- Durable IT deployment reconciliation evidence.
-- Attempts are observations only; they never authorize deployment execution.
create table if not exists it_deployment_reconciliation_evidence (
    id text primary key,
    workspace_id text not null,
    deployment_id text not null,
    service_id text not null,
    provider text not null,
    provider_service_id text not null,
    provider_reference text not null,
    attempt_number integer not null,
    status text not null,
    terminal boolean not null default false,
    observed jsonb not null default '{}'::jsonb,
    verification_state text not null default 'pending',
    evidence jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now()
);
create index if not exists idx_it_recon_evidence_lookup
    on it_deployment_reconciliation_evidence (workspace_id, deployment_id, observed_at desc);
create index if not exists idx_it_recon_evidence_reference
    on it_deployment_reconciliation_evidence (workspace_id, provider_reference, observed_at desc);
