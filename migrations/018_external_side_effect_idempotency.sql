-- Durable external side-effect idempotency for autonomous operations.
-- The key identifies one logical business action, not one delivery attempt.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE publish_outbox
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

UPDATE publish_outbox
SET idempotency_key = 'pub_' || encode(
    digest(workspace_id || '|' || COALESCE(decision_id, '') || '|' || COALESCE(platform, 'facebook') || '|' || COALESCE(brand_id, ''), 'sha256'),
    'hex'
)
WHERE idempotency_key IS NULL;

ALTER TABLE publish_outbox
    ALTER COLUMN idempotency_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_outbox_idempotency
    ON publish_outbox (idempotency_key);

ALTER TABLE publish_outbox
    DROP CONSTRAINT IF EXISTS publish_outbox_status_check;

ALTER TABLE publish_outbox
    ADD CONSTRAINT publish_outbox_status_check
    CHECK (status IN ('pending','processing','published','failed','dead','uncertain'));

ALTER TABLE publish_outbox
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
ALTER TABLE publish_outbox
    ADD COLUMN IF NOT EXISTS locked_by TEXT;

CREATE INDEX IF NOT EXISTS idx_outbox_lease
    ON publish_outbox (status, locked_until)
    WHERE status = 'processing';
