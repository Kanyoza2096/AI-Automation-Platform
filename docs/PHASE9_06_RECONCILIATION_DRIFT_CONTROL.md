# KDO Phase 9.6 — Reconciliation & Drift Control

## Purpose

Phase 9.6 closes the trust loop between:

1. what KDO expected to be true,
2. what the authoritative system reports is true,
3. whether the difference constitutes drift,
4. whether execution may safely resume.

The reconciliation layer produces evidence and a bounded finding. It does not
repair the external system.

## Model

```text
Expected State
     +
Authoritative Observation
     ↓
Reconciliation
     ↓
MATCHED / DRIFT_DETECTED / OBSERVATION_UNAVAILABLE
     ↓
Evidence
     ↓
Recovery / Governance decision
```

## Safety semantics

- MATCHED is the only normal reconciled state.
- DRIFT_DETECTED blocks unsafe execution.
- OBSERVATION_UNAVAILABLE blocks unsafe execution.
- Unknown or missing values are not silently treated as healthy truth.
- Cross-workspace comparisons are forbidden.
- A drift finding does not become RESOLVED merely because it was detected.
- Resolution requires an explicit subsequent reconciliation result.

## Authority boundary

Reconciliation is evidence and state assessment, not execution authority.

It does not:
- call providers,
- mutate resources,
- authorize identities,
- approve ActionIntents,
- bypass Governance,
- bypass Authorization,
- bypass the Action Kernel.

A later remediation must follow the established KDO execution path.

## Critical principle

> KDO must never confuse “we expected X” with “the external world is X.”

Expected state may come from a prior execution/receipt, but authoritative
observation is separately required to establish current truth.

## Non-goals

This phase does not implement:
- live provider polling,
- automatic remediation,
- production reconciliation scheduling,
- database persistence,
- disaster recovery,
- live RLS certification.
