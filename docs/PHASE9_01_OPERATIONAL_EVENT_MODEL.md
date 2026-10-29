# Phase 9.1 — Canonical Operational Event & State Model

## Status
IMPLEMENTED — contract/integrity scope.

## Purpose
Converge existing KDO observability, execution, connector-health and
reconciliation evidence around one provider-neutral operational vocabulary.

This is **not** a new execution engine and does not replace the Action Kernel,
Execution Ledger, Connector Fabric, verification or reconciliation authority.

## Canonical operational states

- `HEALTHY`
- `DEGRADED`
- `UNAVAILABLE`
- `UNKNOWN`
- `RECOVERING`
- `RECONCILIATION_REQUIRED`
- `SECURITY_HOLD`
- `QUARANTINED`

`UNKNOWN` means KDO cannot establish authoritative truth. It is never treated
as healthy or safe for operations requiring authoritative truth.

## Canonical event families

- dependency health observation/state change
- execution state observation
- provider receipt observation
- verification observation
- reconciliation required/completed
- incident opened/updated/resolved
- recovery started/completed
- security hold/quarantine applied

## Authority rule

Operational events are immutable facts/evidence. Their `authority` is always
`none`. They may inform policy and Command Center projections, but they cannot
create authorization or execute a consequential action.

## State transition rule

State changes are validated explicitly. Recovery from an adverse state must
pass through `RECOVERING` or `RECONCILIATION_REQUIRED` where applicable; a
security hold/quarantine cannot silently jump directly to `HEALTHY`.

## Execution safety rule

The operational model exposes `can_execute_safely()` as a policy input only.
The caller must still perform the normal Governance -> Authorization -> Action
Kernel path. `UNKNOWN`, `UNAVAILABLE`, `RECONCILIATION_REQUIRED`,
`SECURITY_HOLD`, `QUARANTINED`, and (when authoritative truth is required)
`DEGRADED` are not safe execution conditions.

## Validation

- New Phase 9.1 tests: **7/7 PASS**
- Existing connector-health/reconciliation/observability/event wiring/workforce
  observability regression set: **34/34 PASS**

## Next Phase 9 step

Build correlated operational telemetry (metrics + traces) on top of this model.
Do not create a second event/state authority in the Command Center.
