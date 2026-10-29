create table if not exists business_learning_signals (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  signal_id text not null,
  pattern_key text not null,
  state text not null check (state in ('LEARNED_PATTERN','MIXED_EVIDENCE','INSUFFICIENT_EVIDENCE','UNKNOWN')),
  confidence text not null,
  sample_size integer not null default 0,
  observed_successes integer not null default 0,
  observed_failures integer not null default 0,
  associated_outcomes integer not null default 0,
  recommendation_ids jsonb not null default '[]'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  objective_id text,
  advisory text not null,
  fingerprint text not null,
  action_authority_granted boolean not null default false check (action_authority_granted = false),
  policy_mutation_allowed boolean not null default false check (policy_mutation_allowed = false),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, signal_id),
  unique (workspace_id, fingerprint)
);
create index if not exists idx_business_learning_signals_workspace_state on business_learning_signals(workspace_id, state, updated_at desc);
