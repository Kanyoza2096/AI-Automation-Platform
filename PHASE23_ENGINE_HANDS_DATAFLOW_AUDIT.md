# KDO Phase 23 — Engine Hands, External Effects & Feedback Audit

## Scope
Traced the publishing execution path beneath Director/Condition/Meta/Guardian into the scheduler, canonical publishing pipeline, execution ledger, connectors, outbox, outcome feedback and AI runtime.

## P0/P1 findings fixed

### P0 — Live publishing could bypass the execution ledger when no idempotency key was supplied
`run_canonical_publish()` accepted a missing `idempotency_key`; `_connector_execute()` then had a direct connector path outside the durable ledger. A live external mutation could therefore execute without the intended idempotency/evidence boundary.

**Fix:** deterministically generate an execution key from tenant + action + brand + platform + content + media URL before any live execution.

### P0 — Missing Guardian was effectively fail-open for live publishing
The fallback returned `warn` and continued despite the canonical pipeline claiming governance hardening. This meant an unavailable Guardian could still permit a live mutation.

**Fix:** unavailable/error Guardian blocks live publishing. Dry-run remains inspectable.

### P0 — Missing Metabolism enforcement could be represented as successful `noop`
The fallback returned `ok/noop`, allowing publishing to proceed even though no quota/metabolism enforcement API was actually bound.

**Fix:** the fallback now reports `unavailable`; live/non-dry execution fails closed through the existing pipeline policy.

## Dataflow verified

Intent → Guardian → Metabolism → Council → DecisionGate/Director → Outbox (durable path) → worker → execution ledger → connector → provider result → outcome → memory/bus/reliability.

The execution ledger explicitly supports at-least-once delivery with durable idempotency and treats provider exceptions as uncertain when reconciliation may be required.

## Important limitation
Exactly-once external side effects cannot be proven offline. Provider-specific idempotency/reconciliation behavior and real staging integrations still require the user's staging environment.

## Tests
- Python compile: PASS
- Full regression suite: 83 passed
- New engine-hands invariants: PASS
