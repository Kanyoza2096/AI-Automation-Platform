-- V65.8: durable decision audit + idempotency.
CREATE TABLE IF NOT EXISTS public.business_decision_audit (
    audit_id text primary key,
    evaluation_id text not null,
    candidate_id text not null,
    understanding_id text not null,
    workspace_id text not null,
    idempotency_key text not null unique,
    evaluation_fingerprint text not null,
    policy_version text not null,
    disposition text not null check (disposition in ('ALLOW','APPROVAL_REQUIRED','REVIEW','BLOCK')),
    authority text not null default 'none' check (authority = 'none'),
    evaluation jsonb not null,
    created_at timestamptz not null default now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_decision_audit_evaluation ON public.business_decision_audit(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_business_decision_audit_workspace_created ON public.business_decision_audit(workspace_id, created_at desc);
CREATE INDEX IF NOT EXISTS idx_business_decision_audit_candidate ON public.business_decision_audit(candidate_id);
