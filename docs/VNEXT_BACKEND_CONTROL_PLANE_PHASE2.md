# KDO Backend vNext — Phase 2 Unified Platform Control Plane

## Objective
Establish one backend-owned, workspace-scoped, read-only platform control-plane projection for the Command Center.

## New canonical route
`GET /api/v1/command-center/platform-control-plane`

## Projection sections
1. Situation
2. Intelligence
3. Governance
4. Execution
5. Connector Fabric
6. Sector Packs
7. Workforce
8. Observability
9. Security & Tenancy
10. Certification

## Authority boundary
The projection is observational only. It does not create ActionIntents, approve actions, execute providers, reconcile executions, or grant authority.

Consequential commands remain owned by Governance, Action Kernel, Durable Execution Ledger, Connector Fabric, Verification and Reconciliation.

## Source rules
- Runtime state comes from the existing production runtime status.
- Decisions, action outbox, execution ledger and workforce projections use workspace-scoped durable tables when available.
- Connector inventory uses the canonical connector factory and connector health helper.
- Sector Pack metadata is loaded from the existing certified portfolio manifests.
- Connector activation/certification registry operator read state remains explicitly `NOT_EXPOSED` because no dedicated operator read contract was present in the supplied backend architecture.
- Missing durable/runtime truth is represented as UNKNOWN/DEGRADED rather than guessed.

## Validation
Targeted architecture and route-contract suite: **27 passed**.

The suite covered:
- unified control-plane section contract
- read-only / no-authority invariant
- sector-pack route contract
- backend route contract
- route wiring audit
- Action Kernel
- execution ledger
- execution operational consistency
- receipt propagation
- domain/application boundary

## Production gate
This is backend source/test validation, not live production certification. Live database/RLS, real provider execution, deployment, infrastructure, and end-to-end staging evidence remain separate gates.
