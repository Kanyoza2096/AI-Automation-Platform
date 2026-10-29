# KDO AI Software Engineering Vertical — Phase 16.9 Integration Report

## Delivered

- Wired the console provider into the FastAPI application factory when `KDO_ENGINEERING_DATABASE_URL` is explicitly configured.
- Added a PostgreSQL-backed provider using the Phase 16.5 task repository; no SQLite/demo fallback is used by the production console routes.
- Added persisted, workspace-scoped console events with sequence uniqueness, advisory-lock sequence allocation, and hash-chain fields.
- Added plan submission API (`POST /api/v1/engineering/console/tasks/{task_id}/plan`) and independent, revision-checked plan review through the existing atomic Phase 16.5 review transaction.
- Added bounded request validation and expected persistence/workflow error mapping.
- Updated the frontend API client and task inspector to support explicit plan submission and clearly show database connection, dynamic planning, and sandbox-execution capability status.
- Added migration `142_kdo_engineering_console_events.sql`.

## Configuration and rollout

1. Apply migrations 138–142 through the normal controlled migration process. Migration 142 depends on the composite task identity introduced in migration 141.
2. Set `KDO_ENGINEERING_DATABASE_URL` to a server-side PostgreSQL connection string available only to the backend. Do not expose it to the browser or frontend build environment.
3. Ensure the runtime role is permitted by the existing RLS/grants to read/write engineering tasks and events. Browser roles remain denied.
4. Deploy the backend and ensure the authenticated Control Plane gateway forwards `/api/v1/engineering/console/*` without stripping the auth/workspace context.
5. Verify the capabilities endpoint reports `database_connected: true`, submit a test task, submit a plan, and review it from a different authorized operator identity.

## Explicit capability boundary

This phase connects persistent intake, events, plan submission, and governed plan review. It does **not** claim that dynamic agent planning, an isolated build worker, test execution, autonomous repair, or deployment authority are connected. The provider reports those capabilities as disabled. A task stays proposed/planned until a separately configured planner/worker lifecycle advances it. The event record is persisted after the authoritative task/approval transaction, not in the same database transaction as that task mutation; reconciliation remains necessary if event persistence fails after a task mutation succeeds.

## Validation performed

- Python compilation passed for `main.py`, `routes/kdo_engineering_console.py`, and `services/kdo_engineering_console_provider.py`.
- Engineering vertical test suite: 178 passed.
- Full repository suite: 346 passed before one pre-existing, unrelated architecture-boundary failure in `tests/architecture/test_phase5_business_onboarding_boundary.py::test_route_is_transport_adapter_not_onboarding_kernel_owner`. That test fails because the existing onboarding route source contains the forbidden string `get_service_client_from_settings`; this phase did not modify that route.
- No live PostgreSQL connection, applied migration, deployed gateway, or production agent/worker was available for verification.
