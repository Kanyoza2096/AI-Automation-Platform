CREATE TABLE IF NOT EXISTS integration_workflow_definitions (workspace_id TEXT NOT NULL, workflow_id TEXT NOT NULL, version TEXT NOT NULL, name TEXT NOT NULL, definition JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(workspace_id,workflow_id,version));
CREATE TABLE IF NOT EXISTS integration_workflow_executions (workspace_id TEXT NOT NULL, execution_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_version TEXT NOT NULL, state TEXT NOT NULL, correlation_id TEXT NOT NULL, step_state JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE integration_workflow_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_workflow_executions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS integration_workflow_definitions_member_select ON integration_workflow_definitions;
CREATE POLICY integration_workflow_definitions_member_select ON integration_workflow_definitions FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
DROP POLICY IF EXISTS integration_workflow_executions_member_select ON integration_workflow_executions;
CREATE POLICY integration_workflow_executions_member_select ON integration_workflow_executions FOR SELECT USING(workspace_id=current_setting('app.workspace_id',true));
CREATE INDEX IF NOT EXISTS idx_integration_workflow_exec_workspace ON integration_workflow_executions(workspace_id,state);
