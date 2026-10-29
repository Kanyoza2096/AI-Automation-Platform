# Kanyoza P24 — Evidence-Driven Recovery and Replanning

## Objective
Make recovery assessment explicitly distinguish handler acknowledgement from verified domain outcome, surface reconciliation requirements, and classify retry safety without triggering retries or bypassing governance.

## Changes

- Recovery assessment now reports the recorded outcome-verification state (`verified`, `ineffective`, `unknown`, or `not_recorded`) separately from whether a handler acknowledged execution.
- It explicitly states that handler acknowledgement is not proof of success.
- Failed, flagged, ineffective, and still-executing tasks are classified `unsafe_until_reconciled`; authoritative domain-state reconciliation is required before considering another attempt.
- Blocked tasks are classified `blocked_by_dependencies`; missing or unsuccessful dependencies require resolution/reconciliation.
- Completed or verified tasks are classified `not_applicable_completed` and do not receive unnecessary recovery work.
- All paths remain read-only: `automatic_action_taken` and `automatic_retry_allowed` remain false. Existing authentication and workspace isolation are preserved.

## Validation

- Focused Director, health, capability, outcome verification, P16–P24 regression suite: **153 passed**.
- Python compilation: passed.
- ZIP integrity: verified.

## Scope and limitations

These are local repository tests. No production adapter, live external service, or live retry was invoked. Retry remains deliberately disabled by this assessment; future execution must still pass ordinary authorization, approval, idempotency, and outcome-verification gates.

## Files changed

- `services/director/core.py`
- `tests/test_phase24_evidence_driven_recovery.py`
