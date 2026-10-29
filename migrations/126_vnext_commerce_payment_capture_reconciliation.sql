create table if not exists commerce_payment_reconciliation_evidence (
    id text primary key,
    workspace_id text not null,
    payment_id text not null,
    provider text not null,
    provider_reference text not null,
    attempt_number integer not null,
    provider_status text not null,
    canonical_status text not null,
    terminal boolean not null default false,
    verification_state text not null default 'pending',
    observed jsonb not null default '{}'::jsonb,
    evidence jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now()
);
create index if not exists idx_commerce_payment_recon_workspace_payment on commerce_payment_reconciliation_evidence(workspace_id, payment_id, attempt_number desc);
