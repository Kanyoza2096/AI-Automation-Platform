# Phase 0 — Runtime Integrity / Stop-the-Line Hardening

Status: **APPLIED AND LOCALLY VERIFIED**

## What Phase 0 now guarantees

| Area | Status | Notes |
|---|---|---|
| FastAPI lifespan boot | PASS | `boot_infrastructure()` is awaited; shutdown is awaited. |
| Health endpoints | PASS | Async health registry is awaited correctly. |
| Production API workers | PASS | Production compose uses one API process until authoritative state is distributed. |
| Production CORE process | PASS | `workers.brain` is a real executable entrypoint. |
| Production WORKERS process | PASS | `workers.scheduler_worker` is a real executable entrypoint. |
| Worker lifecycle | PASS | TaskQueue/outbox/scheduler boot under `SERVICE_MODE=workers`. |
| Scheduler stale-job recovery | PASS | Async JobService recovery is awaited. |
| Scheduler workflow contract | PASS | Scheduler now resolves `PostPublishWorkflowFactory` and consumes `WorkflowResult`. |
| Workflow factory wiring | PASS | `PostPublishWorkflowFactory` is no longer silently aliased to the async content pipeline. |
| Route mounts | PASS | Trend/backup imports use the actual `routes.public/admin` modules. |
| Workspace enumeration | PASS | Unauthenticated root `/workspaces` endpoint removed. |
| WebSocket credentials | PASS | Query-string token fallback removed. |
| Production publish simulation | PASS | Canonical connector simulation defaults to disabled. |
| Durable outbox honesty | PASS | Production enqueue fails closed if persistence is unavailable or fails. |
| SLO shutdown | PASS | Cancelled background tasks are handled cleanly. |
| Autonomous content boot | PASS | Missing `ContentMetrics` runtime symbol fixed. |
| Meta initialization | PASS | Async `stop()` is no longer called un-awaited during sync initialization. |
| Unit/contract tests | PASS | `PYTHONPATH=. pytest -q` → 34 passed. |
| Python compilation | PASS | Modified runtime modules compile successfully. |

## Production deployment contract

```text
api      -> uvicorn main:app --workers 1
core     -> python -m workers.brain
workers  -> python -m workers.scheduler_worker
```

The API remains single-process for now because several authoritative runtime states are still process-local. Phase 1 will move those authorities to durable/distributed stores before API horizontal scaling is enabled.

## Important remaining Phase 1 blockers

Phase 0 intentionally does **not** claim production certification. The next phase must address:

1. Canonical publishing governance across every scheduler/director path.
2. Atomic/durable outbox claiming and multi-worker leasing.
3. Persistent DecisionGate state.
4. Distributed idempotency and quota enforcement.
5. Unified HTTP/WebSocket principal + RBAC/scope authorization.
6. Missing Meta recovery bindings.
7. Remaining synthetic/heuristic telemetry surfaces.
8. Final tenant/RLS convergence.
9. End-to-end integration tests against real service boundaries.
10. Clean dependency installation and production boot in a fresh container/CI environment.
