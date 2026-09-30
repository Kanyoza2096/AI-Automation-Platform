-- V69.14 — integration recovery/reconciliation evidence.
-- The table is a control-plane record only; it never grants execution authority.
create table if not exists integration_recovery_cases (
    recovery_id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    connection_id uuid,
    execution_id text not null,
    exception_id text,
    state text not null check (state in ('open','reconciling','confirmed_success','confirmed_absent','inconclusive','review_required','quarantined','closed')),
    classification text not null,
    idempotency_key text,
    provider text,
    attempts integer not null default 0,
    reason_codes jsonb not null default '[]'::jsonb,
    reconciliation_evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    authority_granted boolean not null default false,
    execution_effect text not null default 'none',
    check (authority_granted = false),
    check (execution_effect = 'none')
);
create index if not exists idx_integration_recovery_workspace_state
    on integration_recovery_cases(workspace_id, state, updated_at desc);
create unique index if not exists uq_integration_recovery_execution
    on integration_recovery_cases(workspace_id, execution_id);

alter table integration_recovery_cases enable row level security;
drop policy if exists integration_recovery_member_select on integration_recovery_cases;
create policy integration_recovery_member_select on integration_recovery_cases
    for select using (workspace_id in (select workspace_id from workspace_members where user_id = auth.uid()));
