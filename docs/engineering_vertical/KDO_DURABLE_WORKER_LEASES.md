# KDO Durable Engineering Worker and Lease Layer

## Scope

Phase 16.3/16.4 provide durable build-job admission and queue adapters. Tenant-bound
workers claim through claim_tenant_scoped_engineering_build_job(...); the SQLite
adapter is single-host development/staging only, while the PostgreSQL adapter is
the intended shared-queue path. This slice adds a bounded worker supervisor around
the existing queue and isolated-worktree workflow rather than creating a second
task repository or a duplicate queue.

## Claim and ownership

A claim is valid only for the returned job identity, tenant, workspace, task,
approved-plan digest, worker ID, lease token, and unexpired lease. Renewal and
completion require the same owner/token fence and reject expired leases. PostgreSQL
renewal/completion now apply the same non-empty identity/token and strict lease
duration validation as SQLite.

The worker supervisor renews the lease in a heartbeat thread while the approved
build callback runs. Each heartbeat uses the latest persisted job record. A failed
renewal is treated as lease loss; the worker is not allowed to publish a review-ready
state after a failed lease check.

## Bounded execution and interruption

run_claimed_build_job(..., max_runtime_seconds=..., heartbeat_interval_seconds=...)
enforces a supervisor deadline. On timeout it stops heartbeats, attempts to complete
the still-owned queue job as uncertain, and returns a timeout error. Uncertain jobs
are not automatically re-queued. The Phase 16.2 workflow accepts an execution guard
and checks it at worktree/executor boundaries and while holding the short
finalization section, so a late executor return cannot promote the task to
awaiting_review after the timeout or lease-loss fence has been set.

**Timeout limitation:** Python cannot safely terminate an arbitrary running thread.
The supervisor therefore bounds how long the caller waits and fences late lifecycle
finalization, but a non-cooperative executor may continue modifying its task worktree
until it returns. That worktree remains isolated from the source repository and is
preserved for reconciliation; the queue is not replayed. Production executors must
also enforce their own process/container command timeouts and cancellation. This
change does not provide hard process termination.

## Separate security boundaries

- **Authorization:** actor scope and action checks remain in the Phase 16.2 workflow
  and governed Action Kernel/Council admission path. A lease is not permission.
- **Execution isolation:** Phase 16.2 creates a task worktree, but a worktree is not
  a security sandbox. The supplied executor must use the separately governed,
  resource-bounded execution environment; this change does not grant arbitrary
  shell, network, secret, or repository-write authority.
- **Review and release:** successful worker execution ends at awaiting_review.
  It does not approve the change, merge it, deploy it, or authorize release.
- **Recovery:** an interrupted or timed-out task is preserved and requires explicit
  inspection/reconciliation. There is no automatic replay or cleanup.

## Verification

Focused tests cover lease heartbeat renewal, timeout-to-uncertain handling,
late-finalization fencing, strict PostgreSQL lease mutation arguments, and the
existing queue/DAG lifecycle. The CI workflow also runs the Phase 16.2 workflow
tests. PostgreSQL locking and lease behavior still require a real supported
PostgreSQL integration test; fake-DB tests are not a substitute.
