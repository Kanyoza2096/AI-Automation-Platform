# KDO Phase 7.6 — Worker Registry & Lifecycle Control

## Objective
Establish a governed inventory and lifecycle plane for worker instances and worker pools so KDO can scale its workforce dynamically without turning worker identity, selection, governance, or execution into one authority.

## Canonical separation
```text
Worker Pool / Worker Registry
        ↓
Worker Identity (V71.1)
        ↓
Declared Capabilities / Permissions (V71.2)
        ↓
Capacity + Selection (V71.9)
        ↓
Governance / Authorization
        ↓
ActionIntent
        ↓
Action Kernel
```

The registry is not an execution engine and does not grant authority.

## New capabilities
- Worker pool definitions with minimum, desired, and maximum instance counts.
- Worker instance inventory with explicit worker kind.
- Lifecycle states: REGISTERED, READY, ACTIVE, DRAINING, SUSPENDED, RETIRED, REVOKED.
- Health states: UNKNOWN, HEALTHY, DEGRADED, UNAVAILABLE.
- Deterministic worker and pool fingerprints.
- Tenant/workspace fencing.
- Identity-integrity checks before lifecycle activation.
- Capacity synchronization with the existing V71.9 capacity registry.
- Unavailable health propagates to selection capacity as UNAVAILABLE.
- Retirement/revocation removes a worker from selectable capacity.
- Lifecycle fence integrated into Worker Selection: only ACTIVE workers with acceptable health may be selected.
- Workforce inventory snapshot for the Command Center/read-model layer.
- Pool membership/count visibility for future autoscaling controllers.

## Security invariants
- `authority_granted = false`
- `execution_requested = false`
- Worker identity does not grant execution authority.
- Worker registry does not authorize actions.
- Worker registry does not call providers.
- Worker registry does not invoke the Action Kernel.
- Worker selection remains advisory; assignment is not authorization.
- Cross-workspace access fails closed.
- Tampered worker/pool records fail closed.

## Validation
- Compileall: PASS
- Phase 7.6 focused + adjacent tests: **38 passed**
- Architecture suite: **456 passed**

## Known validation boundary
A full repository run was attempted but timed out before completion. The architecture gate and focused workforce gate are green; no full-repository green claim is made from this release.

## Result
**PHASE 7.6 — WORKER REGISTRY & LIFECYCLE CONTROL: PASS**

This establishes the foundation for dynamic worker pools and future governed autoscaling without changing the canonical execution authority model.
