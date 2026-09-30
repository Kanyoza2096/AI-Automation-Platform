-- V72.7 durable decision/outcome attribution ledger.
-- Evidence classification only; never authority or causal proof.
CREATE TABLE IF NOT EXISTS business_decision_intelligence_attribution (
    attribution_id text primary key,
    workspace_id text not null,
    recommendation_id text not null,
    decision_id text,
    outcome_id text,
    attribution_state text not null,
    confidence text not null,
    evidence jsonb not null default '[]'::jsonb,
    causal_claim boolean not null default false,
    authority_granted boolean not null default false,
    created_at timestamptz not null default now(),
    CONSTRAINT bdia_state_ck CHECK (attribution_state IN ('OBSERVED','ASSOCIATED','INSUFFICIENT_EVIDENCE')),
    CONSTRAINT bdia_no_causal_claim CHECK (causal_claim = false),
    CONSTRAINT bdia_no_authority CHECK (authority_granted = false),
    UNIQUE (workspace_id, recommendation_id, decision_id, outcome_id)
);
CREATE INDEX IF NOT EXISTS idx_bdia_workspace_recommendation ON business_decision_intelligence_attribution(workspace_id, recommendation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bdia_workspace_outcome ON business_decision_intelligence_attribution(workspace_id, outcome_id, created_at DESC);
ALTER TABLE business_decision_intelligence_attribution ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_decision_intelligence_attribution_workspace_isolation ON business_decision_intelligence_attribution;
CREATE POLICY business_decision_intelligence_attribution_workspace_isolation
ON business_decision_intelligence_attribution FOR SELECT
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
