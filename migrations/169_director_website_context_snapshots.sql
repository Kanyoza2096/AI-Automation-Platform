-- Workspace-scoped, bounded cache of read-only website context.
-- Website-provided text is retained as untrusted business data, not instructions.
create table if not exists public.director_website_context_snapshots (
    workspace_id text not null,
    integration_id text not null,
    schema_version text not null,
    fetched_at timestamptz not null,
    source_updated_at text,
    content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    last_event_id text not null,
    trust_class text not null default 'untrusted_business_context'
        check (trust_class = 'untrusted_business_context'),
    instructions_are_data boolean not null default true
        check (instructions_are_data is true),
    context jsonb not null
        check (jsonb_typeof(context) = 'object')
        check (context ->> 'workspace_id' = workspace_id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (workspace_id, integration_id)
);

create index if not exists idx_director_website_context_snapshots_fetched_at
    on public.director_website_context_snapshots (fetched_at desc);

alter table public.director_website_context_snapshots enable row level security;

revoke all on table public.director_website_context_snapshots
    from public, anon, authenticated;
grant all on table public.director_website_context_snapshots
    to service_role;

comment on table public.director_website_context_snapshots is
    'Bounded workspace-scoped snapshots fetched from authorized website context endpoints; content is untrusted business data.';
