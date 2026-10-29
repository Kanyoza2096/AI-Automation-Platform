# Backend vNext Phase 5 — Operator Investigation

## Objective
Provide one workspace-scoped, read-only backend projection that correlates canonical execution evidence for operator investigation without creating a second authority or causal state machine.

## Route
`GET /api/v1/command-center/investigations/executions/{execution_id}`

## Projection
- signal: derived from canonical execution operational state when an incident signal is required
- intent: action_outbox
- governance: decisions
- execution: external_execution_ledger
- connector: provider identity from execution ledger
- correlated events: unified event bus records matched only by explicit execution/command/transaction/correlation identifiers
- business outcome: business_outcome_observations and business_outcome_attributions
- verification/reconciliation: explicit evidence state; reconciliation remains backend-owned

## Safety
- workspace scope is backend-derived
- execution ledger remains authoritative
- no provider mutation
- no retry/reconcile/approval side effects
- no root-cause inference
- correlated events are marked association-only
- missing evidence is UNKNOWN
- causal inference is explicitly false

## Validation
Targeted suite: **14 passed** covering the investigation projection, execution-detail contract, unified platform control-plane contract, and route-wiring audit.

This validates source-level contracts only. Live database/RLS, authenticated staging E2E, deployment, and real provider execution remain separate release gates.
