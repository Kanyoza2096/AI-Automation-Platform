# Shared PostgreSQL Task and Workspace Repositories

Phase 16.5 adds PostgreSQL implementations of the existing engineering task,
workspace metadata, and plan-review repository contracts.

## Components

- `PostgresEngineeringTaskRepository` persists task contracts, JSONB metadata
  and evidence, idempotency keys, fingerprints, and revision-checked updates.
- `PostgresEngineeringWorkspaceRepository` persists tenant/workspace-scoped
  worktree metadata and first-write-only plan review decisions.
- `migrations/150_kdo_engineering_shared_task_workspace_repositories.sql`
  creates the shared schema, scope indexes, uniqueness constraints, RLS
  enablement, and server-role grants.
- The connection factory is injected. The adapter does not load secrets,
  select a database, or silently fall back to SQLite.

## Safety and current boundary

The PostgreSQL adapters implement repository operations only. They do not
create worker credentials, start a worker, grant code execution authority,
bypass Council/Action Kernel admission, approve patches, or authorize release.
The existing Phase 16.2 workflow remains explicitly composed with the local
SQLite adapters until a separate, reviewed production bootstrap wires the
shared repositories and validates every dependent component.

The migration enables RLS and revokes browser-role access. Production use must
come from the server-side service role through the host's secret-management
boundary. Never expose a service-role credential to browser code.

## Validation

The focused engineering GitHub Actions workflow provisions an ephemeral
PostgreSQL 16 database, applies both the queue migration (140) and this
repository migration (150), then runs the live integration tests. The tests
cover task round-tripping, idempotency, scope isolation, revision-CAS fencing,
workspace metadata updates, and first-write-only review records. Fixture
cleanup is limited to unique tenant/workspace identifiers.

Use only a disposable database for local integration tests. Never point the
suite at production or shared staging data.
