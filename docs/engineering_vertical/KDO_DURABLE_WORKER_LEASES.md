# KDO Durable Engineering Worker and Lease Layer

## Scope and current implementation

The durable engineering execution path is implemented across Phases 16.2–16.4:

- Phase 16.2 owns governed plan review, approval, isolated task worktrees, and the `awaiting_review` boundary.
- Phase 16.3 owns approved-build admission, durable job lifecycle, lease fencing, and bounded supervisor execution.
- Phase 16.4 supplies the shared PostgreSQL queue and task/workspace repository adapters.
- The worker runtime and polling service provide renewable lease heartbeats, scoped polling, backoff, and cooperative shutdown.

SQLite remains a single-host development/test adapter. PostgreSQL is the intended shared queue and repository path. PostgreSQL composition is explicit and dependency-injected; the default application has not been switched to PostgreSQL and no production worker is automatically activated.

## Claim and ownership

A claim is valid only for the returned job identity, tenant, workspace, task, approved-plan digest, worker ID, lease token, and unexpired lease. Renewal and completion require the same owner/token fence and reject expired leases.

The PostgreSQL queue uses transactional row locking with `FOR UPDATE SKIP LOCKED` so concurrent workers can claim independent jobs. Tenant-bound workers filter tenant and workspace in the claim query itself. The polling service rejects malformed or out-of-scope claims before execution.

## Heartbeats, timeouts, and interruption

The worker runtime renews the lease while the approved build callback runs. A failed renewal is treated as lease loss; the worker cannot publish a review-ready state under a lease it may no longer own.

The Phase 16.3 supervisor supports a bounded caller deadline. On timeout it attempts to mark the still-owned job `uncertain`; uncertain work is not automatically replayed. The Phase 16.2 workflow checks an execution guard at worktree/executor boundaries and during final lifecycle writes, preventing a late executor return from promoting the task to `awaiting_review` after timeout or lease loss.

**Hard-timeout limitation:** Python cannot safely terminate an arbitrary running thread. The supervisor bounds how long the caller waits and fences late lifecycle finalization, but a non-cooperative executor may continue modifying its isolated task worktree until it returns. Production execution must therefore use a process/container executor with its own hard command timeout, cancellation, resource limits, and reconciliation path. A Git worktree is not a security sandbox.

## Separate security boundaries

- **Authorization:** actor scope and action checks remain in the governed Phase 16.2 workflow and Council/Action Kernel admission path. A lease is not permission.
- **Execution isolation:** the host must supply a bounded, isolated executor. The worker service does not grant arbitrary shell, network, secret, or repository-write authority.
- **Review and release:** successful worker execution ends at `awaiting_review`. It does not approve patches, merge branches, deploy applications, or authorize release.
- **Recovery:** expired jobs require an authoritative worker-death result before being marked `uncertain`; control-plane errors and unknown status leave state unchanged. The uncertainty transition and audit record are atomic. Replay requires an injected canonical human-approval verifier plus evidence digest; no blind retry or automatic cleanup occurs.
- **Production activation:** the host must explicitly supply the canonical PostgreSQL connection factory, repositories, authorized workflow actor, tenant/workspace scope, and sandboxed executor. No credentials are discovered by these adapters and no SQLite fallback is performed.

## Verified integration coverage

The focused GitHub Actions workflow provisions an ephemeral PostgreSQL 16 service, applies migrations 140, 150, and 151, and exercises the actual PostgreSQL queue/repository adapters. The live integration coverage includes:

- Concurrent `SKIP LOCKED` claims.
- Tenant/workspace isolation.
- Lease expiry and owner/token fencing.
- Expired-worker reconciliation.
- PostgreSQL task/workspace repository operations.
- Atomic plan approval and rollback behavior.
- Explicit PostgreSQL Phase 16.2 workflow composition.

The PR #18 integration run passed 81 tests against ephemeral PostgreSQL 16; the PR #20 focused run passed 86 tests; and the PR #14 worker heartbeat/timeout run passed 77 focused tests. These are separate historical CI runs, not one combined test count.

## Remaining production-readiness work

1. Implement and validate a process/container-backed executor with hard cancellation, CPU/memory/time limits, restricted filesystem and network access, and explicit secret policy.
2. Add an explicit, opt-in worker host/bootstrap that validates all required dependencies and fails closed; do not enable it by default.
3. Phase 17.5 adds scoped uncertain-job listing, worker-death-confirmed reconciliation, atomic append-only application audit events, and verifier-gated human-authorized replay. A production control-plane adapter, evidence store, and approval-service integration still need staging validation.
4. Complete staging validation with the actual deployment topology, PostgreSQL role grants/RLS behavior, worker shutdown/restart, and multi-worker contention.
5. Verify observability and alerting for queue depth, lease renewal failures, uncertain jobs, worker health, and execution duration.
6. Keep merge, deployment, and release approvals separate from build execution. Phase 17.5 replay authorization only requeues a build; it grants no review, merge, deployment, or release authority.

## Deployment note

The GitHub Vercel status for the current `main` head reports a build-rate-limit/upgrade condition. This is an external Vercel account/project quota signal, not evidence that the engineering Python tests failed. Resolve the Vercel quota or project configuration before treating preview deployment as verified.
