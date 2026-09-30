-- Phase 37.0: tenant-scoped publish idempotency.
--
-- The platform treats workspace_id as the tenant boundary. A caller-supplied
-- idempotency key may therefore be reused by two different workspaces without
-- creating a cross-tenant collision. The primary key remains globally unique.

DROP INDEX IF EXISTS uq_publish_outbox_idempotency;

CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_outbox_workspace_idempotency
  ON publish_outbox (workspace_id, idempotency_key);

-- Backfill/upgrade safety: direct database clients must not be able to create
-- a cross-tenant ambiguity by bypassing the application-level namespace.
COMMENT ON INDEX uq_publish_outbox_workspace_idempotency IS
  'Tenant-scoped idempotency boundary for durable publish intents';
