# KDO AI Engineering Vertical — Phase 8: Recovery and Cleanup Hardening

## Implemented

- Added scope-bound `list_status(...)` to the SQLite engineering-task repository, with stored-fingerprint verification and bounded result limits.
- Added `recover_interrupted_engineering_tasks(...)`. It requires an injected worker-liveness probe, treats probe errors and unknown results as live, and only transitions confirmed absent workers from RUNNING to BLOCKED using revision compare-and-swap.
- Recovery records explicitly prohibit automatic resume and require workspace/artifact inspection before retry. This avoids duplicate work when the prior process may have applied a patch or started tests.
- Added `reverse_patch(...)` and changed the orchestration path so denial of test-execution authorization attempts to reverse only the exact patch applied by the current run. It does not use `git reset` or `git clean`, which could destroy unrelated workspace changes.
- Cleanup success/failure is captured as evidence. If patch reversal fails, the run is BLOCKED and manual cleanup is explicitly required.
- Added tests for confirmed orphan recovery, failed liveness probes, live-worker preservation, and patch reversal after authorization denial.

## Validation

Focused regression command:

`pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py`

Result: **65 passed**.

## Remaining release blockers

- The recovery function is a safe recovery primitive, not a production scheduler. Production wiring must supply a trustworthy worker-liveness/lease authority and invoke it from an operational recovery loop.
- The task/artifact adapters remain SQLite local adapters; production multi-instance storage and database-enforced tenant/workspace isolation are not demonstrated by this phase.
- The injected `authorize` callback remains a seam. The source has not established a complete, tested connection from engineering tasks through the authoritative Council approval fence and Action Kernel to a verified receipt.
- Workspace isolation/sandbox enforcement, provider-side-effect reconciliation, authoritative signed receipts, and production crash/concurrency testing remain unproven.
- Automatic resume is intentionally not implemented. A blocked task must be reconciled and its workspace inspected before retry.

## Status

**Implementation hardening complete for this bounded phase; production readiness is not certified.**

## Broader suite note

A repository-wide `pytest -q -x` run reached 346 passing tests before failing in the pre-existing `tests/test_business_onboarding_route_architecture.py::test_route_is_transport_adapter_not_onboarding_kernel_owner`. That test asserts the route source must not contain the string `get_service_client_from_settings`, but the current route includes that name in its documented self-healing resolver and implementation. This failure is outside the files changed in Phase 8. A full `pytest -q` run exceeded the 120-second execution limit and showed another failure later in the run; that second failure was not isolated in this pass. The focused Phase 8 regression suite remains green.
