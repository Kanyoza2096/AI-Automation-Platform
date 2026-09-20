# Phase 37.5 — Controlled Staging Readiness

Phase 37.5 adds a small operational boundary between static release validation
and real staging promotion.

## Principle

Static tests certify code and architecture. They do **not** certify external
infrastructure. Promotion therefore requires explicit authoritative evidence
for each external dependency.

## Readiness states

- `ready`: integrity is valid, security posture meets threshold, and every
  declared dependency has an observed successful probe.
- `blocked`: an observed dependency failed, integrity failed, or security
  posture is below threshold.
- `unobservable`: one or more dependencies have no authoritative observation.
- `degraded`: reserved for a successful evidence set with non-blocking
  warnings.

Unknown is never treated as healthy.

## Authority boundary

The readiness model is advisory. It cannot execute deployments, mutate the
execution ledger, grant credentials, change permissions, or bypass Guardian or
DecisionGate. A real deployment controller remains responsible for promotion.

## Staging checklist

1. Run the static production artifact gate.
2. Build the exact release artifact in staging.
3. Probe the staging database/RLS with service-role authority where required.
4. Probe Redis and distributed locking semantics.
5. Exercise provider adapters with staging/sandbox credentials.
6. Verify webhook idempotency and external execution reconciliation.
7. Run the organism lifecycle against isolated staging tenants.
8. Confirm stop controls and audit logging.
9. Only then treat the release as promotable.
