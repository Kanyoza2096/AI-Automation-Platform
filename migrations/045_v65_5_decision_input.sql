-- V65.5: Decision-plane projection metadata.
-- The decision input is derived from business understanding and must never
-- become an authorization record. Durable event delivery remains the source
-- of truth for downstream consumption.

create table if not exists business_understanding_decision_inputs (
    decision_input_id uuid primary key default gen_random_uuid(),
    understanding_id text not null,
    workspace_id uuid not null,
    message_id text,
    customer_id text,
    intent text not null,
    intent_confidence numeric(5,3) not null check (intent_confidence between 0 and 1),
    governance_status text not null check (governance_status in ('decision_eligible','needs_review','insufficient_evidence')),
    decision_eligible boolean not null,
    governance_score numeric(5,3) not null check (governance_score between 0 and 1),
    resolution_status text not null,
    disagreement boolean not null default false,
    authority text not null default 'none' check (authority = 'none'),
    created_at timestamptz not null default now(),
    unique (workspace_id, understanding_id)
);

create index if not exists idx_business_understanding_decision_inputs_workspace_created
    on business_understanding_decision_inputs(workspace_id, created_at desc);
