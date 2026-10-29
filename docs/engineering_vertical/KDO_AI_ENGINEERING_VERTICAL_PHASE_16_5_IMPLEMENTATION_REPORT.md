# KDO AI Engineering Vertical — Phase 16.5 Implementation Report

## Objective

Add server-side PostgreSQL persistence adapters for engineering task contracts, workspace metadata, and the plan-review ledger so these records can share the canonical database with the Phase 16.4 durable build queue.

## Added files

- `services/kdo_engineering_phase16_5.py`
  - `PostgresEngineeringTaskRepository`: scoped task reads/lists, idempotent create, lifecycle transition and evidence mutation using row locks plus revision compare-and-swap.
  - `PostgresEngineeringWorkspaceRepository`: scope-keyed workspace metadata, constrained status updates, and an append-once approval ledger.
  - Injected DB-API connection factory; per-operation connection lifecycle; no automatic credentials lookup, connection, SQLite fallback, schema creation, execution, or release authorization.
- `migrations/141_kdo_engineering_shared_metadata.sql`
  - PostgreSQL JSONB task metadata, revision/fingerprint fields, tenant/workspace idempotency constraints, workspace metadata, and one-time plan-review ledger.
  - Composite task foreign keys from workspace/review records; RLS enabled; browser roles revoked; intended server-only `service_role` grants.
- `tests/engineering_vertical/test_phase16_5_postgres_metadata.py`
  - Six adapter/migration contract tests.
- `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_5_VALIDATION_REPORT.md`

## Runtime integration contract

1. Apply migrations 140 and 141 using the established migration pipeline.
2. Supply the same canonical PostgreSQL DB-API connection factory to `PostgresEngineeringBuildQueue`, `PostgresEngineeringTaskRepository`, and `PostgresEngineeringWorkspaceRepository`.
3. Inject the PostgreSQL task/workspace adapters into the Phase 16.2 workflow in application composition; do not expose server-side database credentials to browser clients.
4. Preserve authenticated actor authorization and Council/Action Kernel admission. A database row or plan approval is not an execution receipt and does not grant merge, push, deployment, or release authority.

## Important limits

- The repository adapter classes are implemented but **not wired into the application's production bootstrap** in this phase. Existing default Phase 16.2 constructors still use SQLite unless the application explicitly injects these adapters.
- The current workflow records task approval transition and approval-ledger insertion as separate repository operations. They share a database when PostgreSQL adapters are injected, but are not yet one cross-table atomic transaction. Execution remains fail-closed because the workflow requires a matching durable approval ledger before build execution; a failed ledger write can leave a task marked approved without an executable approval record. A subsequent integration phase should add an atomic approve-plan unit-of-work.
- No live PostgreSQL server/DSN or driver was available. SQL migrations, real row locking, RLS/grants, concurrent idempotency, and crash recovery have not been tested against a live database.
- Filesystem/worktree isolation, actor-to-tenant identity binding, worker lease lifecycle integration, observability, and staging acceptance remain separate controls.
- No deployment, merge, push, staging execution, or release authorization was performed.
