-- KDO vNext Phase 4: durable Finance payment lifecycle and reconciliation evidence.
create table if not exists finance_payments (
    id text primary key,
    workspace_id text not null,
    amount double precision not null,
    currency varchar(3) not null,
    reference text not null,
    idempotency_key text not null,
    provider text not null default 'stripe',
    provider_payment_intent_id text,
    status text not null default 'uncertain',
    provider_status text,
    reconciliation_required boolean not null default true,
    evidence_ref text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, idempotency_key)
);

create table if not exists finance_payment_reconciliation_evidence (
    id text primary key,
    workspace_id text not null,
    payment_id text not null,
    provider text not null,
    provider_reference text not null,
    attempt_number integer not null,
    provider_status text not null,
    canonical_status text not null,
    terminal boolean not null default false,
    verification_state text not null,
    observed jsonb not null default '{}'::jsonb,
    evidence jsonb not null default '{}'::jsonb,
    recorded_at timestamptz not null default now(),
    unique (workspace_id, payment_id, attempt_number)
);

create index if not exists idx_finance_payments_workspace_status
    on finance_payments(workspace_id, status);
create index if not exists idx_finance_payment_reconciliation_payment
    on finance_payment_reconciliation_evidence(workspace_id, payment_id, attempt_number desc);

alter table finance_payments enable row level security;
alter table finance_payment_reconciliation_evidence enable row level security;
