-- Phase 47: durable scheduled-post ↔ publish-outbox reconciliation.
ALTER TABLE IF EXISTS scheduled_posts ADD COLUMN IF NOT EXISTS outbox_id TEXT;
ALTER TABLE IF EXISTS scheduled_posts ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS scheduled_posts ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS scheduled_posts ADD COLUMN IF NOT EXISTS error TEXT;
CREATE INDEX IF NOT EXISTS scheduled_posts_outbox_id_idx ON scheduled_posts(outbox_id) WHERE outbox_id IS NOT NULL;
