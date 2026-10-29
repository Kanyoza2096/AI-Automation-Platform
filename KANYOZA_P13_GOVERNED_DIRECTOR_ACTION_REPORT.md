# Kanyoza P13 — Governed Director Action Path

## Scope

P13 closes a governance gap between the engine-signal intake path and the canonical proposal/action handoff.

## Finding

Director already validates `metrics.director_signals/v1` for freshness, source health, capability existence/enabled state, sector consistency, and bounded payload fields. It then creates an advisory task and marks autonomous-capability recommendations as approval-required. However, generic task approval can change the task state to `APPROVED`; without an explicit execution guard, that engine-originated task could proceed down the legacy capability-handler path rather than the proposal-backed governed handoff.

## Change

- Added an execution guard for tasks whose sources include `engine_signal:*` but which have no `proposal_id`.
- Such tasks are deferred back to `APPROVAL`, return the explicit error `engine_signal_requires_proposal_governance`, persist the state, and publish `engine_signal_execution_deferred`.
- This does not auto-create proposals or grant approval. It preserves the recommendation as advisory until a separately governed proposal path is used.
- Added an end-to-end regression test proving a generic approval cannot execute the legacy handler for an engine signal.
- Added a positive governed-handoff test proving an approved proposal calls the canonical `governed_handoff` exactly once, requires canonical `execution_id` and `intent_id` receipt fields for completion, marks the proposal executed, and never falls through to the legacy handler.

## Validation

- `pytest -q tests/test_director_engine_signals.py tests/test_director_execution_idempotency.py tests/test_director_diagnosis_evidence_gate.py tests/test_engine_truth.py`: 55 passed.
- `pytest -q tests/test_director*.py tests/test_engine_truth.py`: 116 passed.
- `python -m compileall -q services/director/core.py tests/test_director_engine_signals.py tests/test_director_execution_idempotency.py`: passed.

## Boundaries

This is source-level and test-level validation, not a live production run. P12's runtime map remains a static inventory; actual instance presence and health must be checked against `/api/v1/topology` and `/api/v1/system-health` after boot. The change intentionally blocks an unlinked engine-signal task rather than inventing a proposal or silently elevating its authority.
