CREATE TABLE IF NOT EXISTS business_process_definitions (
 workspace_id TEXT NOT NULL, process_id TEXT NOT NULL, version TEXT NOT NULL,
 name TEXT NOT NULL, definition JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id, process_id, version)
);
CREATE TABLE IF NOT EXISTS business_process_instances (
 workspace_id TEXT NOT NULL, instance_id TEXT PRIMARY KEY, process_id TEXT NOT NULL,
 process_version TEXT NOT NULL, state TEXT NOT NULL, correlation_id TEXT NOT NULL,
 current_target TEXT, deadline TIMESTAMPTZ, escalation_target TEXT, context JSONB NOT NULL DEFAULT '{}'::jsonb,
 consumed_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb, history JSONB NOT NULL DEFAULT '[]'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS business_process_transition_log (
 workspace_id TEXT NOT NULL, transition_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
 from_state TEXT NOT NULL, to_state TEXT NOT NULL, trigger TEXT NOT NULL, correlation_id TEXT NOT NULL,
 actor_ref TEXT, event_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE business_process_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_process_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_process_transition_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_process_definitions_member_select ON business_process_definitions;
CREATE POLICY business_process_definitions_member_select ON business_process_definitions FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
DROP POLICY IF EXISTS business_process_instances_member_select ON business_process_instances;
CREATE POLICY business_process_instances_member_select ON business_process_instances FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
DROP POLICY IF EXISTS business_process_transition_log_member_select ON business_process_transition_log;
CREATE POLICY business_process_transition_log_member_select ON business_process_transition_log FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
CREATE INDEX IF NOT EXISTS idx_business_process_instances_due ON business_process_instances(workspace_id,state,deadline);
CREATE INDEX IF NOT EXISTS idx_business_process_instances_correlation ON business_process_instances(workspace_id,correlation_id);
