-- V72.5 — durable intelligence recommendation history.
-- Recommendations are evidence records, not decisions or authority.
CREATE TABLE IF NOT EXISTS business_intelligence_recommendations (
    recommendation_id text primary key,
    workspace_id text not null,
    objective_id text,
    domain text not null,
    title text not null,
    rationale text not null,
    state text not null,
    confidence text not null,
    expected_impact double precision,
    capability text not null,
    capability_resolution text not null,
    risk text not null,
    evidence jsonb not null default '[]'::jsonb,
    outcome_links jsonb not null default '[]'::jsonb,
    fingerprint text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    authority_granted boolean not null default false,
    CONSTRAINT bir_no_authority CHECK (authority_granted = false),
    CONSTRAINT bir_state_ck CHECK (state IN ('OBSERVED','DERIVED','ADVISORY','UNKNOWN','CAPABILITY_UNAVAILABLE','GOVERNANCE_REVIEW_REQUIRED')),
    UNIQUE (workspace_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_bir_workspace_updated ON business_intelligence_recommendations(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_bir_workspace_domain ON business_intelligence_recommendations(workspace_id, domain, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_bir_workspace_objective ON business_intelligence_recommendations(workspace_id, objective_id, updated_at DESC);
ALTER TABLE business_intelligence_recommendations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_intelligence_recommendations_workspace_isolation ON business_intelligence_recommendations;
CREATE POLICY business_intelligence_recommendations_workspace_isolation
ON business_intelligence_recommendations FOR SELECT
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
