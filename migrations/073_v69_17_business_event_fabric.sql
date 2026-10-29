-- V69.17 — durable cross-system business event fabric
create table if not exists business_event_subscriptions (
  workspace_id text not null,
  subscription_id uuid not null,
  event_type text not null,
  destination text not null,
  enabled boolean not null default true,
  max_hops integer not null default 8,
  require_sequence boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (workspace_id, subscription_id),
  check (max_hops between 1 and 16)
);
create table if not exists business_events (
  workspace_id text not null,
  event_id text not null,
  event_type text not null,
  source text not null,
  correlation_id text not null,
  causation_id text,
  sequence bigint,
  occurred_at timestamptz not null,
  hop integer not null default 0,
  trace jsonb not null default '[]'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  payload_fingerprint text not null,
  created_at timestamptz not null default now(),
  primary key (workspace_id, event_id),
  check (hop between 0 and 16)
);
create index if not exists idx_business_events_workspace_type on business_events(workspace_id,event_type,occurred_at);
create table if not exists business_event_deliveries (
  workspace_id text not null,
  delivery_id uuid not null,
  event_id text not null,
  subscription_id uuid not null,
  destination text not null,
  state text not null,
  attempt integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (workspace_id, delivery_id),
  unique (workspace_id,event_id,subscription_id),
  foreign key (workspace_id,event_id) references business_events(workspace_id,event_id),
  foreign key (workspace_id,subscription_id) references business_event_subscriptions(workspace_id,subscription_id)
);
create index if not exists idx_event_deliveries_pending on business_event_deliveries(workspace_id,state,created_at);

alter table business_event_subscriptions enable row level security;
alter table business_events enable row level security;
alter table business_event_deliveries enable row level security;

-- Production deployments must bind these policies to the existing KDO workspace/member context.
-- No credential or provider-authority columns are present in this schema.
