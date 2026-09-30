create table if not exists business_decision_policy_evaluations (
    evaluation_id text primary key,
    candidate_id text not null,
    understanding_id text not null,
    workspace_id text not null,
    customer_id text,
    action text not null,
    risk_level text not null,
    policy_version text not null,
    disposition text not null check (disposition in ('ALLOW','APPROVAL_REQUIRED','REVIEW','BLOCK')),
    reason text not null,
    approval_required boolean not null default false,
    review_required boolean not null default false,
    governance_status text,
    candidate_status text,
    constraints jsonb not null default '[]'::jsonb,
    authority text not null default 'none' check (authority = 'none'),
    created_at timestamptz not null default now()
);
create index if not exists idx_business_decision_policy_evals_workspace_created
    on business_decision_policy_evaluations(workspace_id, created_at desc);
create index if not exists idx_business_decision_policy_evals_candidate
    on business_decision_policy_evaluations(candidate_id);
