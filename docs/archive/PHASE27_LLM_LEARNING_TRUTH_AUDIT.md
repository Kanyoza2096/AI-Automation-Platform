# Phase 27 — LLM → Agent → Director → Council → Learning Truth Audit

## Finding fixed
`strategy_executed` was treated as proof of success. The event is emitted when a governed strategy pipeline accepts/dispatches a strategy and may contain a decision/job/outbox id, but it does not itself prove that an external effect succeeded.

### Risk
The autonomous loop incremented `decisions_successful` immediately and could feed that signal into learning before an authoritative provider outcome existed.

### Fix
The handler now treats `strategy_executed` as dispatch/acceptance only. Success learning and the success counter require an explicit `outcome=success` plus `verified=true`. Authoritative `outcome.recorded` remains the primary feedback path.

## Dataflow rule now enforced
LLM recommendation → governed decision → concrete capability → execution → provider result → verification/reconciliation → `outcome.recorded` → learning.

LLM output never directly constitutes execution success or learning success.

## Verification
- Python compilation: PASS
- Test suite: 85 passed, 0 failed
