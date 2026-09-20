-- Unified durable execution ledger. One row represents one logical external
-- action; attempts are represented by the attempt counter and evidence fields.
CREATE TABLE IF NOT EXISTS external_execution_ledger (
  execution_id TEXT PRIMARY KEY,
  workspace_id UUID NOT NULL,
  action TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'started',
  attempt INTEGER NOT NULL DEFAULT 0,
  provider TEXT,
  provider_request_id TEXT,
  provider_object_id TEXT,
  error TEXT,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT external_execution_status_check CHECK
    (status IN ('started','succeeded','failed','uncertain','cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_external_execution_workspace
  ON external_execution_ledger(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_execution_reconcile
  ON external_execution_ledger(status, updated_at)
  WHERE status = 'uncertain';

CREATE OR REPLACE FUNCTION begin_external_execution(
  p_workspace_id UUID, p_action TEXT, p_idempotency_key TEXT,
  p_provider TEXT DEFAULT NULL, p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO external_execution_ledger
    (execution_id, workspace_id, action, idempotency_key, provider, evidence)
  VALUES
    ('exe_' || encode(gen_random_bytes(12), 'hex'), p_workspace_id,
     p_action, p_idempotency_key, p_provider, p_metadata)
  ON CONFLICT (idempotency_key) DO NOTHING;
  RETURN QUERY SELECT * FROM external_execution_ledger
    WHERE idempotency_key = p_idempotency_key;
END;
$$;

CREATE OR REPLACE FUNCTION complete_external_execution(
  p_execution_id TEXT, p_status TEXT, p_provider_request_id TEXT DEFAULT NULL,
  p_provider_object_id TEXT DEFAULT NULL, p_error TEXT DEFAULT NULL,
  p_evidence JSONB DEFAULT '{}'::jsonb
) RETURNS SETOF external_execution_ledger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE external_execution_ledger
  SET status = p_status, attempt = attempt + 1,
      provider_request_id = COALESCE(p_provider_request_id, provider_request_id),
      provider_object_id = COALESCE(p_provider_object_id, provider_object_id),
      error = p_error, evidence = COALESCE(p_evidence, '{}'::jsonb), updated_at = now()
  WHERE execution_id = p_execution_id;
  RETURN QUERY SELECT * FROM external_execution_ledger WHERE execution_id = p_execution_id;
END;
$$;

ALTER TABLE external_execution_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS execution_ledger_workspace_isolation ON external_execution_ledger;
CREATE POLICY execution_ledger_workspace_isolation ON external_execution_ledger
  USING (workspace_id = auth.uid() OR EXISTS (
    SELECT 1 FROM workspace_members wm
    WHERE wm.workspace_id = external_execution_ledger.workspace_id
      AND wm.user_id = auth.uid()
  ));
