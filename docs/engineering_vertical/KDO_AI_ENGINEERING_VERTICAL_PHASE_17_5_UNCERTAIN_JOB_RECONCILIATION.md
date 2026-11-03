# Phase 17.5 — Uncertain Job Reconciliation and Authorized Recovery

## Why this phase exists

A worker can disappear after claiming a job and before persisting its final result. Lease expiry alone is not proof that execution stopped: the worker may be partitioned from PostgreSQL while its process or container is still running. KDO must not automatically replay such work.

## Reconciliation behavior

- Expired running jobs are candidates only; a worker-control-plane callback must explicitly return `False` to confirm the worker is dead.
- A callback error, unknown value, or `True` leaves the job untouched.
- The state transition is fenced by job, tenant, workspace, original lease token, running status, and database-confirmed lease expiry.
- The transition from `running` to `uncertain` and its audit event commit in one PostgreSQL transaction.
- Reconciliation can be tenant/workspace scoped and processes a bounded batch.
- A scoped query lists uncertain jobs for operator review.

## Human-authorized replay

The only implemented recovery action is replay to the queue's `queued` state. It requires:
- A non-empty human actor identity and approval reference.
- A 64-character SHA-256 evidence digest.
- An injected authorization verifier that validates the canonical approval and replay action.
- Evidence, referenced by digest, that the old process/container is terminated, the task workspace is quarantined and inspected, and replay is safe.

The verifier must return the literal boolean `True`; errors and all other values fail closed. The queue update and `replay_authorized` audit insert are atomic. The original job identity, idempotency key, and attempt count are preserved. Reconciliation never automatically retries a job.

## Audit model

Migration 152 creates `public.kdo_engineering_job_reconciliation_events`. It records:
- Worker-death confirmation (`running` → `uncertain`) with system actor.
- Human-authorized replay (`uncertain` → `queued`) with actor, approval reference, and evidence digest.

The service role receives SELECT/INSERT only; UPDATE and DELETE are revoked. Browser roles receive no access. Database owners remain trusted administrators and can alter schema/privileges; this is an application append-only boundary, not tamper-proof external evidence storage.

## Security and operational limitations

- A worker-death callback is only as authoritative as the host/control-plane implementation. A database timeout or heartbeat miss must never be mapped to “dead”.
- Replay is unsafe until the prior execution has been stopped and the workspace/artifacts inspected. The injected verifier is a mandatory integration point; a permissive stub must never be used in production.
- This phase does not supply a production worker-control-plane API, UI, evidence store, or approval service. It provides the fail-closed queue and audit contract for those integrations.
- Replaying a job does not approve code, merge a branch, deploy, or release.
- Production execution remains disabled pending Phase 17.6 task-board visibility and Phase 17.7 staging validation.

## Validation

Focused PostgreSQL 16 integration tests cover unknown worker status, atomic uncertain transition and audit, authorization denial, authorized replay, preserved attempt count, and replay audit evidence.
