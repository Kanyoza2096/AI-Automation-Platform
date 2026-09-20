# Kanyoza Roadmap Implementation — V73+ Consolidation

## Objective

Finish the agreed intelligence-to-execution convergence before adding another
feature generation. The system must have one consequential execution boundary:

`Event -> Rule -> Decision -> Governance -> ActionIntent -> Action Kernel -> Durable ActionOutbox -> Connector -> Provider Receipt -> Reconciliation -> Outcome -> Attribution -> Learning`

## Implemented in this release

- Canonical V65.9 decision-handoff admission through V73 convergence.
- Trusted business-action context and deterministic scheduling policy.
- Approval-required handoffs cannot be silently promoted to autonomous execution.
- Action intent materialization is allow-listed and fail-closed.
- Duplicate admission uses the existing Action Kernel idempotency boundary.
- Website/CMS is a first-class connector with governed `website_publish` and
  `website_update` actions.
- Website mutation routes create Action Kernel intents; they do not call the CMS
  directly.
- Website updates support partial PATCH-like changes while requiring at least one
  mutable field.
- Provider success requires an authoritative content object ID; ambiguous
  transport remains uncertain/reconciliation-required.
- Website publication/update is high-risk and approval-gated.
- Business Automation catalog includes approved website publish/update templates.
- Outcome measurement, conservative attribution, and advisory learning remain
  downstream of execution truth and cannot grant authority.

## Existing roadmap capabilities retained

- V65 bounded AI business understanding.
- V66 outcome measurement and learning.
- V67 autonomous exception/recovery foundations.
- V68 production autonomy certification fixtures.
- V69 connector activation/certification/health/runtime gateway.
- V70 business operating layer.
- V71 production runtime and workforce execution convergence.
- V72 command-center truth/read-model and production execution certification gate.

## Final certification gates

The repository is not declared production-certified merely because offline tests
pass. Real certification requires:

1. PostgreSQL-backed ActionOutbox concurrency with independent workers.
2. Real lease ownership/recovery and restart behavior.
3. A staging website/CMS connection with real credentials.
4. One successful website publication with provider content ID evidence.
5. Duplicate delivery proving one external side effect.
6. Timeout-after-commit proving reconciliation to the same provider object.
7. Outcome event and attribution in the same workspace.
8. Full regression suite passing after stale test contracts are reconciled.

## Deliberate boundaries

- AI does not own execution authority.
- Learning cannot mutate policy or permissions.
- Website connector does not expose delete by default.
- HTTP 2xx is not sufficient evidence of external success.
- Missing trusted business parameters produce review/blocked outcomes rather than
  fabricated values.
