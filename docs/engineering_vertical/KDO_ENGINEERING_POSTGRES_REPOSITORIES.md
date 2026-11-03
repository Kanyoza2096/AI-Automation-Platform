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
- `migrations/151_kdo_engineering_plan_approval_task_scope.sql` binds approval
  records to their task scope because approval precedes worktree creation.
- `create_postgres_phase162_workflow` explicitly composes the existing governed
  Phase 16.2 workflow with both PostgreSQL repositories.
- The connection factory and authorization policy are injected. The adapter
  does not load secrets, select a database, or silently fall back to SQLite.

## Safety and current boundary

The PostgreSQL adapters implement repository operations only. They do not
create worker credentials, start a worker, grant code execution authority,
bypass Council/Action Kernel admission, approve patches, or authorize release.
The PostgreSQL task repository implements `review_plan_atomically`: the task
lifecycle transition and immutable approval-ledger insert commit in one
transaction. The ledger references the task scope rather than a workspace row,
because the workflow approves a plan before it creates the execution worktree.
The existing application/default workflow remains explicitly composed with the
local SQLite adapters. A host may opt into PostgreSQL by calling
`create_postgres_phase162_workflow` with its managed connection factory,
authorization policy, and workspace paths. This is dependency composition only,
not a production worker bootstrap or activation of a deployment.

The migration enables RLS and revokes browser-role access. Production use must
come from the server-side service role through the host's secret-management
boundary. Never expose a service-role credential to browser code.

## Validation

The focused engineering GitHub Actions workflow provisions an ephemeral
PostgreSQL 16 database, applies both the queue migration (140) and this
repository migration (150), then runs the live integration tests. The tests
cover task round-tripping, idempotency, scope isolation, revision-CAS fencing,
workspace metadata updates, first-write-only review records, and atomic plan
approval before workspace creation. Fixture
cleanup is limited to unique tenant/workspace identifiers.

Use only a disposable database for local integration tests. Never point the
suite at production or shared staging data.
