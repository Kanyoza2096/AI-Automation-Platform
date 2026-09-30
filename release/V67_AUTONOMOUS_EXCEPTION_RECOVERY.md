# V67 — Autonomous Exception & Recovery

## Objective
Introduce bounded autonomous exception handling without moving authority into AI or the recovery planner.

## Safety contract
- AI remains outside the side-effect boundary.
- Exception classification is deterministic and evidence-based.
- Uncertain or partial external state is reconciled before retry.
- Irreversible/high-impact recovery requires governed approval.
- Validation/policy failures are blocked rather than retried.
- Autonomous retry has a hard budget of three attempts and exponential backoff.
- Unknown repeated failures are quarantined.
- Recovery plans carry `authority=none` and cannot execute a side effect by themselves.
- Any actual retry/reconciliation must still pass through DecisionGate/Council as applicable and Action Kernel → durable outbox → ExecutionLedger/provider.

## Implemented
- `services/autonomous_exception_recovery.py`
  - deterministic exception classification
  - stable exception fingerprints/IDs
  - bounded recovery planning
  - uncertain-state protection
  - retry budget/quarantine
  - fail-closed handling of auth, validation, policy and unknown failures
- `migrations/056_v67_exception_recovery.sql`
  - durable exception evidence
  - durable recovery plans
  - workspace RLS
- `tests/test_autonomous_exception_recovery.py`

## Explicit non-goals
V67 does not grant the AI, exception classifier, learning system, or recovery planner authority to perform external side effects. It also does not claim that an external state is reconciled merely because a plan exists.
