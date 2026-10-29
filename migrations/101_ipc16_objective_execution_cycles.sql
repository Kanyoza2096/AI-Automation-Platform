-- IPC-16: durable identity for governed autonomous objective cycles.
CREATE TABLE IF NOT EXISTS objective_execution_cycles (
  cycle_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  objective_id TEXT NOT NULL,
  source_replan_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  governed_decision_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  cycle_fingerprint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(workspace_id, source_replan_id),
  UNIQUE(workspace_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_objective_execution_cycles_workspace_objective
  ON objective_execution_cycles(workspace_id, objective_id, created_at DESC);
