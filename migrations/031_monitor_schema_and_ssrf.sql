-- Phase 19: monitor runtime/schema contract hardening.
-- The monitor code consumes these fields directly; keep them explicit and indexed.
ALTER TABLE IF EXISTS monitor_targets
  ADD COLUMN IF NOT EXISTS url TEXT,
  ADD COLUMN IF NOT EXISTS expected_status INTEGER NOT NULL DEFAULT 200,
  ADD COLUMN IF NOT EXISTS is_paused BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE monitor_targets
SET url = COALESCE(url, config->>'url')
WHERE url IS NULL AND config ? 'url';

UPDATE monitor_targets
SET expected_status = CASE
  WHEN config->>'expected_status' ~ '^[0-9]+$' THEN (config->>'expected_status')::INTEGER
  ELSE 200
END
WHERE config ? 'expected_status';

UPDATE monitor_targets
SET is_paused = CASE
  WHEN lower(config->>'is_paused') IN ('true','false') THEN (config->>'is_paused')::BOOLEAN
  ELSE NOT COALESCE(enabled, TRUE)
END
WHERE config ? 'is_paused' OR enabled IS NOT NULL;

CREATE INDEX IF NOT EXISTS monitor_targets_workspace_idx ON monitor_targets(workspace_id);
CREATE INDEX IF NOT EXISTS monitor_targets_active_idx ON monitor_targets(workspace_id, is_paused);

ALTER TABLE IF EXISTS monitor_checks
  ADD COLUMN IF NOT EXISTS error_message TEXT;
