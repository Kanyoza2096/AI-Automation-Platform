# KDO Phase 2 — Distributed Authority Hardening

## Scope
Phase 2 makes authoritative state safer across multiple processes: DecisionGate persistence, atomic usage accounting, durable webhook replay protection, and database migration support.

## Changes
- DecisionGate now persists issued decisions to `decisions` and reloads decisions not present in process memory.
- Production decision persistence fails closed instead of silently minting process-local authority.
- Decision outcomes are persisted alongside the decision record.
- Added `022_phase2_distributed_authority.sql` with decisions, webhook idempotency, atomic usage RPCs, indexes, and RLS.
- Metabolism post/token consumption uses atomic database RPCs when a database is configured, preventing check-then-increment races.
- Production denies usage operations when the distributed usage store is unavailable.
- Webhook idempotency uses an atomic database claim when available and fails closed in production if the durable store is unavailable.
- Existing in-memory fallbacks remain available for development/test environments.

## Validation
- Python bytecode compilation: PASS
- Existing regression suite: 34 passed
- No test dependencies or fake services were introduced.

## Production migration requirement
Apply all migrations through `022_phase2_distributed_authority.sql` before enabling multi-instance production. The application must use the service-role/database connection for authoritative internal writes while preserving workspace scoping at the application boundary.

## Remaining Phase 2 gates
- Run the RPCs against a real Postgres/Supabase instance.
- Add database-backed integration tests for concurrent decision lookup, quota consumption, webhook replay, and outbox leasing.
- Verify all tenant-bearing tables and RLS policies against the final production schema.
- Verify Redis-backed rate limiting and distributed event sequence/replay semantics.
