# KDO AI Engineering Vertical — Phase 7 Report

## Source-grounded integration work

This phase inspected the actual source APIs and corrected the artifact adapter to match
`SQLiteEngineeringArtifactRepository.put(...)` / `get(...)` / `list_task(...)`, rather than
using the previously assumed `put_artifact(...)` signature.

Added `services/kdo_engineering_persisted_workflow.py` to wire the existing Phase 2 task repository,
Phase 3 artifact repository, Phase 5 Git adapter, Phase 4 command runner, and Phase 6 orchestrator.
The workflow:
- requires a persisted, scope-matching task in PLANNED or QUEUED state;
- uses revision compare-and-swap transitions to claim RUNNING;
- checks the injected authorization callback before patch application and test execution;
- persists snapshot, patch, test log, and review report using the actual artifact API;
- moves the task to AWAITING_REVIEW only when tests pass, otherwise FAILED or BLOCKED;
- never marks the task COMPLETED.

## Validation

Integration tests use temporary Git repositories and the actual SQLite task/artifact adapters.
Exact test output is recorded in the Phase 7 validation file.

## Important boundaries

The task/artifact adapters are local SQLite implementations, not production multi-instance storage.
The authorization callback remains an explicit seam; this phase does not prove the real KDO Action Kernel,
Council approval fence, Director, or authoritative execution ledger are connected. No authoritative signed
receipt is created. A task can reach COMPLETED only through a separately verified review/receipt workflow.
The runner remains non-sandboxed by itself and must run in an enforced isolated container/VM.

## Next work

Inspect and connect the actual KDO authorization/execution-ledger contracts if present in the source. If they
are absent or cannot be invoked safely, record that as a release blocker. Add durable job recovery,
workspace cleanup after blocked runs, idempotent artifact replay, and full receipt/review verification before
claiming production readiness.
