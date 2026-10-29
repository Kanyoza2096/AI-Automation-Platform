create table if not exists public.commerce_order_notification_deliveries (
    id text primary key,
    workspace_id text not null,
    order_id text not null,
    customer_id text not null,
    to_number text not null,
    body text not null,
    idempotency_key text not null,
    provider text not null default 'twilio',
    provider_message_sid text,
    provider_status text,
    status text not null default 'pending',
    reconciliation_required boolean not null default true,
    evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz,
    unique(workspace_id, idempotency_key)
);
create index if not exists idx_commerce_order_notification_pending on public.commerce_order_notification_deliveries(workspace_id, reconciliation_required, created_at);
create table if not exists public.commerce_order_notification_reconciliation_evidence (
    id text primary key,
    workspace_id text not null,
    delivery_id text not null,
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
create index if not exists idx_commerce_order_notification_recon_evidence on public.commerce_order_notification_reconciliation_evidence(workspace_id, delivery_id, attempt_number desc);
alter table public.commerce_order_notification_deliveries enable row level security;
alter table public.commerce_order_notification_reconciliation_evidence enable row level security;
