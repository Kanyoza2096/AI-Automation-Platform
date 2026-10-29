-- V70.6 Content Intelligence & Marketing Operating Loop
-- Business-context persistence only. Provider execution remains in canonical
-- Action Kernel -> durable outbox -> connector pipeline.

create table if not exists marketing_objectives (
    objective_id text primary key,
    workspace_id text not null,
    name text not null,
    outcome text not null,
    audience text not null default '',
    offer text not null default '',
    channels jsonb not null default '[]'::jsonb,
    constraints jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists idx_marketing_objectives_workspace on marketing_objectives(workspace_id);

create table if not exists content_marketing_briefs (
    brief_id text primary key,
    workspace_id text not null,
    objective_id text not null references marketing_objectives(objective_id),
    topic text not null,
    audience text not null default '',
    offer text not null default '',
    channels jsonb not null default '[]'::jsonb,
    context text not null default '',
    correlation_id text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists idx_content_marketing_briefs_workspace on content_marketing_briefs(workspace_id);
create index if not exists idx_content_marketing_briefs_objective on content_marketing_briefs(objective_id);

create table if not exists content_marketing_candidates (
    candidate_id text primary key,
    workspace_id text not null,
    brief_id text not null references content_marketing_briefs(brief_id),
    topic text not null,
    caption text not null,
    channels jsonb not null default '[]'::jsonb,
    evidence_fingerprints jsonb not null default '[]'::jsonb,
    confidence numeric(5,4) not null default 0,
    lifecycle text not null default 'governance_pending',
    governance_required boolean not null default true,
    publish_request jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (confidence >= 0 and confidence <= 1),
    check (lifecycle in ('brief','intelligence_ready','draft_ready','governance_pending','published','failed'))
);
create index if not exists idx_content_marketing_candidates_workspace on content_marketing_candidates(workspace_id);
create index if not exists idx_content_marketing_candidates_brief on content_marketing_candidates(brief_id);
