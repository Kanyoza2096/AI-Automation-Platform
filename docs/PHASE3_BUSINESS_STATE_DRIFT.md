# Phase 3 — Business State & Drift Engine

## Purpose

Phase 3 makes **desired business state vs authoritative observed business state** a first-class KDO read model.

It is a convergence layer, not another state database or reconciliation engine.

## Existing primitives reused

- `services.phase9_reconciliation.ExpectedState` — desired state contract
- `services.phase9_reconciliation.AuthoritativeObservation` — authoritative observation contract
- `services.phase9_reconciliation.reconcile` — fail-closed comparison and drift classification
- `services.business_process_capability_graph.BusinessProcessCapabilityGraphProjector` — business topology/context
- existing Action Kernel, execution ledger, evidence and provider reconciliation boundaries remain authoritative for execution

## What Phase 3 adds

`BusinessStateDriftProjector` produces `BusinessStateDriftSnapshot` with:

- workspace isolation
- desired resources
- latest authoritative observation per resource
- MATCHED / DRIFTED / UNKNOWN / OBSERVATION_UNAVAILABLE state health
- differing fields and evidence references
- explicit unsafe-execution blocking when reconciliation is unresolved
- optional explicit graph-node resolution
- deterministic ordering
- coverage and warnings

## Non-goals

This layer does **not**:

- store a second copy of business state
- mutate desired state
- execute remediation
- authorize actions
- retry providers
- infer resource identity from names
- replace the existing reconciliation services

## Authority boundary

The Business State & Drift read model is observational. An unresolved drift finding is a signal to existing governance/reconciliation/execution boundaries; it is not permission to remediate itself.
