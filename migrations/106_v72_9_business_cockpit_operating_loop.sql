-- V72.9 Business Cockpit operating-loop evidence ledger.
-- Read-model support only; it cannot grant authority or execute commands.
CREATE TABLE IF NOT EXISTS business_cockpit_work_items (
    item_id text primary key,
    workspace_id text not null,
    kind text not null,
    state text not null,
    priority text not null,
    recommendation_id text,
    decision_id text,
    evidence jsonb not null default '[]'::jsonb,
    command_intent jsonb,
    fingerprint text not null,
    action_authority_granted boolean not null default false CHECK (action_authority_granted = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    UNIQUE (workspace_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_business_cockpit_work_items_workspace_state
  ON business_cockpit_work_items(workspace_id, state, priority, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_cockpit_work_items_recommendation
  ON business_cockpit_work_items(workspace_id, recommendation_id, updated_at DESC);
ALTER TABLE business_cockpit_work_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_cockpit_work_items_workspace_isolation ON business_cockpit_work_items;
CREATE POLICY business_cockpit_work_items_workspace_isolation
ON business_cockpit_work_items FOR SELECT
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
