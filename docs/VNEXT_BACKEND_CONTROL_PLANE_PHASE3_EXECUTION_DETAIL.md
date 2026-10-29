# KDO Backend vNext — Phase 3 Execution Detail Evidence Chain

## Objective

Provide one canonical, read-only backend contract for the Command Center to trace a consequential operation across the existing authoritative control-plane records:

`Intent → Governance → Action Kernel → Connector → Provider Receipt → Canonical Outcome → Verification → Reconciliation`

## Contract

`GET /api/v1/command-center/executions/{execution_id}`

The route is workspace-scoped and derives the workspace from the authenticated request. It first resolves the durable `external_execution_ledger` row, then correlates related `action_outbox`, governance decision, `business_outcome_observations`, and `business_outcome_attributions` evidence within the same workspace.

## Authority rules

- `external_execution_ledger` remains the source of truth for external execution state.
- The route is read-only and grants no action or reconciliation authority.
- Provider receipt evidence is not treated as proof of business success.
- `uncertain` execution remains uncertain and produces `reconciliationRequired=true`.
- Missing evidence is represented as `UNKNOWN`; no synthetic success/status/timestamp is generated.
- Business outcome verification is sourced from persisted outcome observations and preserves `authority=none`.
- No retry, reconcile, approve, execute, or provider call is performed by the read contract.

## Read-model sections

1. `intent`
2. `governance`
3. `actionKernel`
4. `connector`
5. `providerReceipt`
6. `canonicalOutcome`
7. `verification`
8. `reconciliation`
9. `identity`

## Validation

Targeted backend validation for this phase: **43 tests passed**.

Coverage included:
- execution-detail section and authority contract
- uncertain execution / reconciliation semantics
- missing-evidence `UNKNOWN` semantics
- workspace isolation in the read model and route contract
- backend contract route declarations
- route wiring audit
- Action Kernel behavior
- durable execution plane
- execution operational consistency
- receipt propagation invariant
- domain/application boundary

This is source-level automated validation only. It does not certify live deployment, production database/RLS configuration, real provider execution, staging E2E, or device behavior.
