# KDO AI Engineering Vertical — Phase 17.6 Task Board

## Delivered in this phase

The first-party engineering console now enriches persisted task records with the authoritative PostgreSQL build-job projection from `kdo_engineering_build_jobs`.

- Task cards show the durable build-job state and attempt count alongside the engineering task lifecycle state.
- Task detail responses include the current build-job projection.
- A worker is exposed as an active Build Worker only while its queue row is `running`, has a lease owner, and has a lease expiry later than the projection time.
- Expired leases, queued jobs, terminal jobs, and rows without an owner do not produce active-worker claims.
- Queue reads are filtered by both tenant and workspace and are performed as one bounded query for the current task page.
- The projection is read-only. It does not renew leases, claim work, change task state, authorize replay, approve plans, or grant merge/deployment/release authority.
- No new migration is required; this uses the existing Phase 16.4 shared queue schema.

## Evidence and semantics

The queue is authoritative for job status, attempt count, worker lease owner, and lease expiry. The task repository remains authoritative for the task's objective, acceptance criteria, lifecycle status, revision, and persisted plan evidence.

The console does not infer specialist-agent activity from a worker lease. A worker lease proves only that a worker identity currently holds a valid queue lease. The UI must continue to show no specialist agent activity unless explicit agent lifecycle events are supplied by an authoritative runtime source.

The queue projection fails closed: if the PostgreSQL queue read fails, the provider returns an unavailable error rather than silently presenting a stale or incomplete board as current.

## Validation

Focused tests cover:
- A valid lease produces one active Build Worker projection.
- An expired lease does not produce an active worker.
- Queued jobs and ownerless jobs do not produce active-worker claims.
- Queue projection query includes tenant and workspace predicates and binds the requested task IDs.
- The existing engineering console test module remains part of focused Engineering Vertical CI.

## Remaining integration boundary

This phase makes queue state visible; it does not start or enable a worker. Production execution remains disabled until the separate Phase 17.7 staging acceptance gates are met, including real staging validation, process/container hard cancellation, tenant isolation, observability, worker supervision, and independent release approval.
