create table if not exists commerce_fulfillment_reconciliation_evidence (
  id text primary key,
  workspace_id text not null,
  order_id text not null,
  provider text not null,
  provider_reference text not null,
  attempt_number integer not null,
  provider_status text not null,
  canonical_status text not null,
  terminal boolean not null default false,
  verification_state text not null,
  observed jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null default now()
);
create index if not exists idx_commerce_fulfillment_recon_ws_order on commerce_fulfillment_reconciliation_evidence(workspace_id, order_id, attempt_number desc);
