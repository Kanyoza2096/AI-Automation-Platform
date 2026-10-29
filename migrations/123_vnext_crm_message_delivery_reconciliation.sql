-- Durable CRM outbound message delivery and provider reconciliation evidence.
create table if not exists crm_message_deliveries (
    id text primary key,
    workspace_id text not null,
    contact_id text not null,
    to_number text not null,
    body text not null,
    idempotency_key text not null,
    provider text not null default 'twilio',
    provider_message_sid text,
    provider_status text not null default 'unknown',
    status text not null default 'pending',
    reconciliation_required boolean not null default true,
    evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, idempotency_key)
);

create table if not exists crm_message_reconciliation_evidence (
    id text primary key,
    workspace_id text not null,
    delivery_id text not null,
    provider text not null default 'twilio',
    provider_reference text not null default '',
    attempt_number integer not null,
    provider_status text not null,
    canonical_status text not null,
    terminal boolean not null default false,
    verification_state text not null,
    observed jsonb not null default '{}'::jsonb,
    evidence jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now(),
    unique (workspace_id, delivery_id, attempt_number)
);

create index if not exists idx_crm_message_delivery_reconciliation
    on crm_message_deliveries(workspace_id, reconciliation_required, created_at);
create index if not exists idx_crm_message_evidence_delivery
    on crm_message_reconciliation_evidence(workspace_id, delivery_id, attempt_number desc);

alter table crm_message_deliveries enable row level security;
alter table crm_message_reconciliation_evidence enable row level security;
