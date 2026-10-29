# KDO AI Engineering Vertical — Phase 16.4 Implementation Report

## Scope

Phase 16.4 adds a migration-managed PostgreSQL adapter for the Phase 16.3 governed engineering build queue. It is designed for a canonical shared PostgreSQL database and uses database transactions, `FOR UPDATE SKIP LOCKED`, unique tenant/workspace idempotency constraints, lease expiry, and lease-token fencing. The connection factory and Action Kernel are explicit dependencies; the code does not discover secrets, open production connections automatically, or fall back to SQLite.

## Changed files

1. `services/kdo_engineering_phase16_4.py` — new `PostgresEngineeringBuildQueue`, scoped queue reads, idempotent enqueue, concurrent claim, fenced renewal/completion, conservative expired-job reconciliation, fail-closed readiness check, and explicit Action Kernel contract registration.
2. `migrations/140_kdo_engineering_shared_build_queue.sql` — canonical PostgreSQL table, tenant/workspace uniqueness, lease-state constraint, queue/recovery indexes, RLS enabled, browser roles revoked, server-side `service_role` grants.
3. `tests/engineering_vertical/test_phase16_4_shared_queue.py` — seven tests for fail-closed configuration, SQL locking/fencing, scoped operations, explicit Action Kernel registration and migration security constraints.
4. `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_4_IMPLEMENTATION_REPORT.md` — this report.
5. `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_4_VALIDATION_REPORT.md` — validation commands and limitations.

## Runtime contract

- Apply migration `140_kdo_engineering_shared_build_queue.sql` through the project's normal migration pipeline before wiring this adapter.
- Supply a server-only DB-API connection factory backed by the canonical PostgreSQL database (psycopg 3 or psycopg2 compatible). This package does not contain a driver, DSN, or production credential.
- Call `register_postgres_build_enqueue_contract(...)` explicitly during application bootstrap. The existing Phase 16.3 admission path must still invoke Council deliberation and verify the canonical Action Kernel execution receipt before enqueueing.
- The queue adapter is shared-storage-ready at the code/SQL level, but the production connection, database grants, and actual multi-process behavior must be proven in staging before use.
- Jobs with expired leases are marked `uncertain` only when an injected liveness probe explicitly returns `False`; unknown/failed probes do not recover or replay jobs.
- No automatic retry, worktree deletion, merge, push, deployment, or release authorization is introduced.

## Boundaries / not implemented

- The current Phase 16.2 task and workspace metadata repositories remain SQLite adapters. Phase 16.4 does **not** migrate the entire task/approval/workspace ledger to PostgreSQL, so this is not yet an end-to-end multi-host production control plane.
- No live database was available for migration execution or concurrency testing. The environment did not have `psycopg` or `psycopg2` installed.
- No production database URL, service-role secret, live Council service, canonical receipt verifier, or staging environment was supplied. No live readiness claim is made.
- Production OS/container/VM sandboxing, authenticated principal integration, worker supervision/heartbeats, tracing/metrics/alerts, operational runbooks, and staging security/failure-injection acceptance remain required.
- The queue uses `service_role` server-side access. Never expose that key to a browser. RLS is enabled and `anon`/`authenticated` table grants are revoked; deploy-time policy/grant verification remains required.

## Release boundary

No deployment, merge, push, staging execution, or release authorization was performed. The readiness helper reports configuration shape only and always sets `release_authorized` to `False`.
