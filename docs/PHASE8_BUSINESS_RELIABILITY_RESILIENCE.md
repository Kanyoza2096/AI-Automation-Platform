# Phase 8 — Business Reliability & Resilience

Phase 8 converges KDO's existing dependency health, SLO, reconciliation,
recovery and resilience-test primitives into a business-level reliability
read model.

## Authority

The Business Reliability layer is **projection/read-model only**. It does not
replace or mutate dependency health, SLO metrics, reconciliation, recovery,
execution, or provider systems. It cannot grant execution authority.

## Model

`BusinessReliabilityObjective` defines explicit business reliability targets.
`ReliabilitySignal` represents evidence from an existing authoritative
subsystem. `BusinessReliabilityAssessment` classifies the combined state as
HEALTHY, DEGRADED, CRITICAL, UNKNOWN, or BLOCKED.

Unknown and reconciliation debt are never silently converted into success.
Cross-workspace evidence is rejected. Assessment fingerprints are deterministic
for the same logical evidence set.

## Reuse

The projector consumes existing KDO primitives rather than introducing a new
health engine, SLO engine, recovery engine, reconciliation engine, or execution
engine.
