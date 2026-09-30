# KDO Wave 13 — Distributed Execution & Concurrency Certification

## Objective

Certify that the canonical external execution plane remains safe when multiple
workers race the same idempotency key, when a provider accepts a request but
the worker loses the response, and when an execution lease expires.

## Certified invariants

1. **One logical execution per workspace + idempotency key**
   Concurrent `begin_external_execution` calls converge on one execution identity.
2. **Single active executor**
   A live lease held by worker A causes worker B to return `in_progress` rather than
   invoke the provider.
3. **Provider side effect is not duplicated by a transport ambiguity**
   A provider timeout after acceptance becomes `uncertain`; replay returns the same
   uncertain execution and does not call the provider again.
4. **Lease expiry is quarantined**
   A second worker cannot simply take over expired in-flight work. The execution is
   transitioned to `uncertain` and requires reconciliation.
5. **Stale-worker completion is fenced**
   Durable completion requires the current worker identity and an unexpired lease.
6. **Tenant-scoped idempotency**
   The same idempotency key may exist independently in different workspaces.

## Validation

- New Wave 13 distributed-race certification: **6/6 passed**
- Combined targeted concurrency/recovery/architecture certification: **52/52 passed**
- Python `compileall`: **PASS**
- Broad architecture/security/reliability invocation: exceeded the 120-second
  execution harness timeout after progressing through the suite; it was not used
  as a pass/fail signal.

## Result

The canonical execution plane is certified for the tested distributed race classes.
The external side effect remains at-least-once at the platform boundary; exact
external duplication semantics remain dependent on provider idempotency and the
reconciliation contract.
