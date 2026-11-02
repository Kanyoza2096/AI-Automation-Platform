# KDO Engineering DAG Readiness Coordinator

## Purpose

`services/kdo_engineering_dag_coordinator.py` adds the first integration slice between persisted engineering-task contracts, the specialist registry, and dependency-aware scheduling. It validates a tenant/workspace-scoped task DAG and moves only eligible planned tasks into the persisted `queued` state.

## Behaviour

- Requires each task to declare `metadata.role_id` and optional `metadata.depends_on`.
- Validates unique task IDs, registered role IDs, dependency existence, and acyclic ordering before state transitions.
- Re-reads each task from the repository and rejects stale supplied revisions.
- Queues a planned task only when all dependencies are `completed` and its specialist adapter is installed.
- Leaves tasks waiting when dependencies have not completed.
- Blocks planned/queued descendants when a prerequisite is failed, blocked, or cancelled.
- Enforces tenant/workspace scope and uses repository revision compare-and-swap transitions.
- Reports unavailable specialist adapters without silently substituting another role.

## Explicit limitations

This component is a **readiness coordinator**, not a durable worker queue or executor. It does not claim leases, start adapters, create isolated workspaces, run code, or authorize release. The SQLite repository is a local/test adapter; production must use KDO's approved durable database and worker/lease infrastructure. A queued task still requires a governed worker to claim it and execute it in an isolated workspace.

## Acceptance tests added

The focused tests cover dependency ordering, waiting for prerequisites, failure propagation, invalid DAG rejection, missing adapters, stale revisions, and tenant/workspace isolation. Test execution must be verified by CI or a local test run before this change is considered validated.
