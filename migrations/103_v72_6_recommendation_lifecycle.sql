-- V72.6 durable recommendation lifecycle + outcome feedback.
ALTER TABLE business_intelligence_recommendations
  ADD COLUMN IF NOT EXISTS lifecycle text NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE business_intelligence_recommendations
  ADD COLUMN IF NOT EXISTS last_event_id text;
ALTER TABLE business_intelligence_recommendations
  ADD COLUMN IF NOT EXISTS outcome_evaluation jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $$ BEGIN
  ALTER TABLE business_intelligence_recommendations
    ADD CONSTRAINT bir_lifecycle_ck CHECK (lifecycle IN ('ACTIVE','ACKNOWLEDGED','DISMISSED','SUPERSEDED'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS business_intelligence_recommendation_events (
    event_id text primary key,
    workspace_id text not null,
    recommendation_id text not null,
    event_type text not null,
    actor_id text not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    authority_granted boolean not null default false,
    CONSTRAINT bire_no_authority CHECK (authority_granted = false),
    UNIQUE (workspace_id, event_id),
    FOREIGN KEY (recommendation_id) REFERENCES business_intelligence_recommendations(recommendation_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bire_workspace_recommendation ON business_intelligence_recommendation_events(workspace_id, recommendation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bire_workspace_created ON business_intelligence_recommendation_events(workspace_id, created_at DESC);
ALTER TABLE business_intelligence_recommendation_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_intelligence_recommendation_events_workspace_isolation ON business_intelligence_recommendation_events;
CREATE POLICY business_intelligence_recommendation_events_workspace_isolation
ON business_intelligence_recommendation_events FOR SELECT
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
