-- V71.1 — governed AI workforce agent identity
create table if not exists agent_identities (
    workspace_id text not null,
    agent_id text not null,
    name text not null,
    purpose text not null,
    agent_class text not null default 'business_worker',
    version text not null default '1',
    status text not null default 'proposed',
    declared_capabilities jsonb not null default '[]'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    identity_fingerprint text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, agent_id),
    check (status in ('proposed','active','suspended','retired'))
);
create index if not exists idx_agent_identities_workspace_status
    on agent_identities(workspace_id, status);

-- IMPORTANT: this table is identity metadata only. It grants no tool, provider,
-- Action Kernel, approval, or execution authority. Those controls are separate.
