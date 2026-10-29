# KDO vNext — Connector Fabric Phase 3: Contract/Evidence Hardening

## Scope

This increment deepens the Connector Fabric foundation without moving execution authority into the Command Center or connector read model.

Roadmap chain:

Provider -> Capability -> Action Contract -> Provider Adapter -> Health -> Certification -> Receipt -> Verification -> Reconciliation

## Changes

- Connector Fabric Action Contract views are now derived from the canonical Action Kernel registration helpers instead of maintaining a second copy of action risk/parameter/approval facts.
- Reconciliation capability is surfaced only when the existing provider adapter exposes a concrete reconciliation method.
- GitHub workflow mutations map to the existing `reconcile_workflow` adapter contract.
- Render deployment mutations map to the existing `reconcile_deploy` adapter contract.
- Provider adapters are inspected as classes only; no credentials are loaded and no provider calls are made.
- Health and certification remain `NOT_EXPOSED` because this read model does not yet have a durable workspace-scoped operator read contract for those observations.
- Connector Fabric remains read-only and authority-free.

## Verification

Targeted Connector Fabric/certification/health/adapter tests: 59 passed.

Python compilation: passed.

No live provider calls were performed.
