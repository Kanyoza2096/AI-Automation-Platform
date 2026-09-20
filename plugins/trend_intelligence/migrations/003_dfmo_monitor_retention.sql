
-- Optional: mark DFMO probes for safe purge
ALTER TABLE IF EXISTS monitor_checks
  ADD COLUMN IF NOT EXISTS source TEXT;

CREATE INDEX IF NOT EXISTS monitor_checks_source_checked_idx
  ON monitor_checks (source, checked_at);
