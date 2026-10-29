# KDO AI Engineering Vertical — Phase 16.3 Implementation Report

## Scope

Phase 16.3 adds a durable single-host queue adapter and an explicit Council + Action Kernel admission path for approved engineering builds. It builds on Phase 16.2 and does not replace the production Action Kernel, Council, database, execution ledger, or queue infrastructure.

## Implemented

- `SQLiteEngineeringBuildQueue` persists jobs, tenant/workspace/task scope, approved-plan digest, idempotency key, attempts, lease owner, fencing token, and lease expiry.
- Enqueue is idempotent for the same scoped task/digest and rejects conflicting idempotency payloads or a second distinct job for the same task.
- Claim uses a SQLite immediate transaction and a per-claim fencing token. Renewal and completion require the current owner/token and an unexpired lease.
- Expired jobs are marked `uncertain` only after an injected liveness probe explicitly confirms worker absence. Unknown/failed probes do not recover or replay jobs.
- `register_engineering_build_enqueue_contract` explicitly registers a high-risk Action Kernel contract with `requires_approval=True` and `inline_with_decision=True`. It must be called during bootstrap with the selected queue; importing the module does not register or enable the action.
- `admit_approved_build` verifies actor authorization, task state, approved plan digest, and the durable independent-review record, then calls the existing Council-governed Action Kernel bridge. The bridge requires a fresh Council decision and a verified authoritative execution receipt.
- `run_claimed_build_job` rechecks worker lease, scope, task state, plan digest, and durable reviewer record before execution. It finishes at `awaiting_review`; it never merges, pushes, deploys, or authorizes release.
- The persisted workflow module re-exports the Phase 16.3 APIs as the integration entry point.

## Files changed in Phase 16.3

1. `services/kdo_engineering_phase16_3.py` — new queue, lease fencing, governed admission, worker runner.
2. `services/kdo_engineering_persisted_workflow.py` — exports Phase 16.3 APIs.
3. `tests/engineering_vertical/test_phase16_3_worker_queue.py` — 11 queue, lease, tenant-isolation, persistence, governance and worker-lifecycle tests.
4. `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_3_IMPLEMENTATION_REPORT.md` — this report.
5. `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_3_VALIDATION_REPORT.md` — commands and validation evidence.

## Operational and security limits

- SQLite is a durable local adapter, not a multi-host distributed queue. It must not be used as the production queue for multiple worker replicas.
- The application must wire the Action Kernel to the canonical durable ledger and configure the receipt verifier against the authoritative execution record store. A missing or uncertain receipt fails closed.
- Council/Action Kernel admission depends on the configured Council adapter and registered Action Kernel contract. This package does not configure production credentials, register routes, or expose an HTTP API.
- The authorization callback must be bound to authenticated identities and authoritative tenant/workspace policy. Test callbacks are not production authorization.
- Worker execution must be bounded and monitored. If a lease expires during a long-running executor, completion is rejected and reconciliation is required; the system does not automatically replay the task.
- Git worktrees are not an OS/container sandbox. Build-agent isolation, resource/network/credential restrictions and artifact validation remain required.
- No staging validation, production deployment, merge, push, or release authorization was performed.
