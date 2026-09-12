# KDO Phase 6 — Distributed Failure-Storm Hardening

## Objective
Attack the authoritative publish/outbox path under worker crashes, expired leases, connector ambiguity, and concurrent workers. The primary rule is: **never trade correctness for automatic recovery when an external side effect may already have happened.**

## Findings and fixes

### P0 — Expired worker leases could be retried blindly
A worker can die after the external provider accepts a publish but before KDO persists the result. Reclaiming an expired `processing` row as retryable can create a duplicate post.

**Fix:** expired `processing` rows are quarantined as `uncertain`. They are not automatically claimed again.

### P0 — Lost worker ownership could allow stale finalization
A delayed worker could finish after its lease was lost and overwrite the durable row.

**Fix:** durable completion/failure RPCs require both `status='processing'` and the exact `locked_by` lease owner.

### P0 — Connector failure/timeout was automatically retryable
An HTTP timeout is ambiguous: the provider may have accepted the request even if KDO received no response.

**Fix:** publish failures are quarantined as `uncertain` instead of automatically retried. This is intentionally conservative until provider-specific idempotency/reconciliation is available.

### Explicit recovery path
Species operators can reconcile an uncertain item:
- `published` + provider post ID → records the confirmed external side effect.
- `confirmed_not_published` → explicitly requeues the item.

No automatic reconciliation is attempted.

## Failure matrix

| Failure | Expected behavior | Phase 6 |
|---|---|---|
| DB unavailable before enqueue | fail closed | PASS |
| Worker crashes while processing | lease expires → uncertain | PASS |
| Worker loses lease before completion | stale completion rejected | PASS |
| Connector timeout | uncertain, no blind retry | PASS |
| Connector explicit failure | uncertain, no blind retry | PASS |
| Duplicate workers | row-level claim + lease ownership | PASS |
| Operator confirms no publish | explicit requeue | PASS |
| Operator confirms publish | record provider ID | PASS |

## Validation

- Python compilation: PASS
- Regression suite: **43 passed**
- Phase 6 failure-storm tests: PASS
- Durable lease ownership contract: PASS
- Expired-lease quarantine contract: PASS

## Remaining live-infrastructure gates

These require a real staging environment and cannot be honestly certified in an isolated sandbox:

1. Two or more API/worker processes against the same PostgreSQL/Supabase database.
2. Kill a worker after durable claim and before/after connector invocation.
3. Kill DB connectivity during finalization.
4. Exercise actual provider timeout/connection-reset behavior.
5. Verify provider-specific idempotency or reconciliation for each connector.
6. Verify RLS policies using real authenticated JWTs for multiple workspaces.
7. Verify Redis outage and recovery across multiple instances.

## Production policy

Until every connector has provider-level idempotency or a reliable lookup/reconciliation mechanism, **automatic retries of ambiguous external publishes must remain disabled**. Availability can be restored later on a connector-by-connector basis without weakening correctness.
