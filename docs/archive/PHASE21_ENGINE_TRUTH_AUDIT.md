# KDO Phase 21 — Engine Truth & Execution Semantics

## Scope
Deep audit of major engine promises and the complete dataflow from observation through action and verification.

## Findings fixed

1. Condition Engine `execute_plan_async()` previously changed a plan to `APPROVED` when no executor was bound. Approval is not execution; it now fails with `action_executor_unavailable`.
2. Condition Engine previously marked a plan `EXECUTED` for any returned result, including an explicit `{ok: false}`. It now records `FAILED` for negative results.
3. Meta Engine recovery previously marked recovery `SUCCESS` immediately after the handler returned and only later attempted health verification. If verification failed the status remained `SUCCESS` with an error. It now changes that state to `FAILED`; only `VERIFIED` represents a confirmed recovery.

## Dataflow contract

Observation -> Condition Engine -> decision -> Council -> ActionPlan -> governed executor -> external/service hand -> outcome -> verification -> durable learning.

Infrastructure: Meta snapshot -> anomaly -> diagnosis -> incident -> governed Guardian recovery -> bounded executor -> health verification -> recovery outcome.

AI: recommendation -> Autonomous Loop -> strategy execution -> request/authority boundary -> DecisionGate/outbox -> execution -> reconciliation -> outcome.

## Truth rules

- `APPROVED` means permission, never execution.
- `EXECUTED` means the bound executor returned a conclusive positive result.
- `VERIFIED` is required to claim successful recovery.
- Missing executor, negative result, or failed verification cannot be represented as success.
- Advisory/unsupported capabilities must report unavailable rather than pretending to perform the requested task.

## Verification

Python compilation: PASS
Automated suite: 81/81 PASS
