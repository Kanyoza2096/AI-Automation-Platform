-- V65.6 Decision Candidate ledger.
-- Candidates are proposals for the KDO decision plane, never approvals.
create table if not exists business_decision_candidates (
    candidate_id text primary key,
    workspace_id text not null,
    understanding_id text not null,
    message_id text,
    customer_id text,
    correlation_id text,
    intent text not null,
    candidate_action text not null,
    risk_level text not null,
    requires_approval boolean not null default false,
    status text not null check (status in ('proposed','review_required','blocked')),
    intent_confidence numeric not null default 0,
    governance_score numeric not null default 0,
    governance_status text not null,
    resolution_status text,
    disagreement boolean not null default false,
    signals jsonb not null default '[]'::jsonb,
    constraints jsonb not null default '[]'::jsonb,
    reason text not null default '',
    authority text not null default 'none' check (authority = 'none'),
    created_at timestamptz not null default now()
);
create index if not exists idx_business_decision_candidates_workspace_created
    on business_decision_candidates(workspace_id, created_at desc);
create index if not exists idx_business_decision_candidates_understanding
    on business_decision_candidates(understanding_id);
