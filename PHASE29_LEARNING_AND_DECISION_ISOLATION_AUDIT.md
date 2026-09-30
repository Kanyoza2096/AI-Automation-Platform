# Phase 29 — Learning and Decision Isolation Audit

## Scope
Deep audit of Condition Engine strategy selection, outcome feedback, brand health events, and the LLM/learning boundary.

## Findings fixed

### P1 — Strategy selection was not brand-scoped
`select_strategy(brand_id)` considered every enabled strategy. Strategy trial/reward state could therefore influence a different brand.

**Fix:** strategy selection now accepts only strategies explicitly scoped to the requested brand or explicitly global (no `metadata.brand_id`).

### P1 — Strategy learning could contaminate other brands
`_learn_from_decision()` mapped actions to global strategy IDs and updated their UCB statistics without checking the decision's brand.

**Fix:** strategy statistics are updated only when the strategy is global or explicitly belongs to the decision's brand.

### P2 — Empty-brand health event emitted the wrong field
The health object uses `score`, while the event publisher read `health_score`, causing an empty brand health event to report `0` instead of `50`.

**Fix:** event now publishes `health["score"]`.

## Dataflow after fix

Observation(brand) -> baseline/trend/anomaly -> decision(brand) -> strategy selection(brand) -> governed action -> verified outcome(brand) -> learning update(brand strategy) -> future brand decisions.

No strategy learning may cross an explicit brand boundary.

## Verification
- Python compilation: PASS
- Regression tests: 85 passed
