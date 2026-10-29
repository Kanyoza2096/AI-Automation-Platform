# KDO Phase 9.4 — Reliability & Dependency State

## Purpose

Phase 9.4 establishes authoritative operational health for dependencies used by KDO.

This state is a safety input and operational fact. It is **not** an execution authority.

## Canonical boundary

Provider / Connector evidence
→ Health Observation
→ Dependency Health
→ Safety Predicate / Operational Policy
→ existing Governance + Authorization + Action Kernel

The health layer never directly calls a provider and never authorizes an ActionIntent.

## States

- HEALTHY
- DEGRADED
- UNAVAILABLE
- UNKNOWN
- RECOVERING
- RECONCILIATION_REQUIRED
- SECURITY_HOLD
- QUARANTINED

The conservative rule is:

> UNKNOWN is not healthy.

For strict authoritative dependency operations, only `HEALTHY` is execution-safe.

## Reliability invariants

1. Workspace scope is mandatory.
2. Dependency identity is explicit.
3. Health records are authority-free.
4. Evidence references are retained.
5. Correlation identifiers can be propagated.
6. Older observations cannot overwrite newer state.
7. State updates are immutable/revisioned.
8. Cross-workspace updates are rejected.
9. Cross-dependency updates are rejected.
10. Health never grants execution permission.

## Why this matters

The system can now distinguish:

Provider is healthy
→ normal policy evaluation

Provider is degraded
→ unsafe/high-risk work may be held

Provider is unavailable
→ execution requiring the dependency must not proceed

Provider state is unknown
→ KDO must not assume healthy

Reconciliation required
→ dependent execution is unsafe until truth is restored

Security hold / quarantine
→ the dependency is explicitly prevented from being treated as operationally safe

## Non-goals

This phase does not claim:
- live provider health polling
- Prometheus/OpenTelemetry deployment
- automatic remediation
- disaster recovery
- chaos testing
- live database/RLS certification
- execution authority
