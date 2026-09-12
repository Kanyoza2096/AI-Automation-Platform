# Phase 28 — Intelligence Boundary & Learning Integrity Audit

## Findings fixed

1. `task_completed` was treated as verified success by the learning loop. A Director local completion only proves the local capability handler returned an acceptable result. Success learning now requires an explicit verified outcome.
2. `outcome.recorded` could mutate a decision before checking workspace ownership. Workspace validation now occurs before mutation, preventing cross-tenant feedback from changing another workspace's decision.

## Canonical rule
LLM/agent recommendation -> governed decision -> concrete capability -> execution -> authoritative provider result -> verification/reconciliation -> outcome.recorded -> learning.

`task_completed` is operational telemetry, not proof of external success.

## Verification
- Python compilation: PASS
- Regression suite: 85 passed
