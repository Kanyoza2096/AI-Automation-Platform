-- V71.18 — Control-plane restart convergence.
-- Existing V71 tables are the authoritative stores; this migration adds indexes
-- used during workspace hydration and integrity/restart inspection. No authority
-- or execution state is introduced here.
CREATE INDEX IF NOT EXISTS idx_agent_identities_workspace_updated
  ON agent_identities(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_capability_permissions_workspace_updated
  ON agent_capability_permissions(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_memories_workspace_created
  ON agent_memories(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_plans_workspace_updated
  ON agent_plans(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_budgets_workspace_updated
  ON agent_budgets(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_human_takeover_workspace_updated
  ON agent_human_takeover_cases(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_coordination_workspace_updated
  ON agent_coordination_requests(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_autonomous_runs_workspace_updated
  ON agent_autonomous_runs(workspace_id, updated_at);
