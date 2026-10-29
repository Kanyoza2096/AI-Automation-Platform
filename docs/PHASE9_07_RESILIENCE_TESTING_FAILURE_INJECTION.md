# KDO Phase 9.7 — Resilience Testing & Failure Injection

## Purpose

Phase 9.7 proves that Phase 9 safety contracts behave correctly under
controlled failure conditions.

The test harness models:

- timeouts
- connection failures
- error responses
- stale data
- inconsistent data
- partial outages
- recovery states

## Critical boundary

The resilience harness is **not** an execution engine.

It cannot:
- authorize a caller,
- call a provider,
- mutate production state,
- approve an ActionIntent,
- bypass Governance,
- bypass Authorization,
- bypass the Action Kernel.

It produces deterministic test evidence about how the existing policy and
reconciliation contracts respond.

## Safety expectations

Examples:

```text
UNAVAILABLE + HIGH RISK
        → BLOCK

UNKNOWN
        → HOLD

RECOVERING
        → RECOVERY_REQUIRED / HOLD

SECURITY_HOLD
        → SECURITY_HOLD / BLOCK

QUARANTINED
        → QUARANTINE / BLOCK

HEALTHY + stale authoritative observation
        → RECONCILIATION_REQUIRED
```

This last case is important: a dependency may appear healthy from a reliability
signal while authoritative state has drifted. Reconciliation therefore remains
a separate safety boundary.

## Determinism

The same scenario and state must produce the same policy outcome. Randomness
is used only for evidence identifiers, never for safety decisions.

## Non-goals

This phase does not claim:
- live chaos testing,
- production fault injection,
- provider shutdown control,
- automatic remediation,
- disaster recovery certification,
- live database/RLS certification.

A future production chaos system should be separately authorized, tenant-aware,
time-bounded, and protected by explicit safety gates.
