# Kanyoza P23 — Verified Execution Outcomes and Unknown-Evidence Safety

## Objective
Ensure Director actions are learned as successful or ineffective only when the outcome predicate has enough authoritative evidence to decide. A handler's `ok: true` is execution acknowledgement, not proof of the intended domain outcome.

## Changes

### 1. Three-state outcome predicates
The restart, schedule-update, and worker-pool verification predicates now distinguish:
- `True`: fresh, trusted observations prove the requested post-state.
- `False`: authoritative, comparable observations prove the requested state was not reached (including a measured mismatch or workspace mismatch).
- `None`: evidence is absent, stale, unhealthy, incomplete, or otherwise insufficient to decide.

This prevents missing telemetry from being mislabeled as an actual failed action.

### 2. Unknown outcome blocks false learning
When an action handler reports success but verification is unknown, Director marks the task `FLAGGED`, records `outcome_verification.outcome=unknown`, and retains a reason. It does not promote the action to verified or ineffective. `LearningLoop` already excludes flagged tasks from outcome signals, so neither success nor failure is learned from missing evidence.

A predicate exception follows the same unknown path rather than becoming a false negative. Proposal state is not marked executed when its outcome is unknown.

### 3. Explicit verification receipts
Task results now record a structured verification outcome for verified, ineffective, and unknown post-action states. A verified outcome requires the predicate to return the literal boolean `True`; a literal `False` represents measured ineffectiveness; all other values are treated conservatively as unknown.

### 4. Regression coverage
Added `tests/test_phase23_verified_outcome_unknown.py` and updated the existing outcome-verification contract tests to assert unknown for missing/stale/unhealthy evidence. Coverage includes measured mismatches, missing observations, predicate exceptions, and prevention of learning success/failure from unknown outcomes.

## Validation
- Focused Director, capability, health, signal-publisher, learning, and outcome-verification regression suite: **173 passed**.
- Python compilation: passed for Director modules and P23 tests.
- ZIP integrity: verified after packaging.

## Scope and limitations
- These are local repository tests, not production execution tests.
- This phase does not claim live service/worker adapters, Supabase, provider APIs, or observer freshness in production are available.
- `FLAGGED` means the action's intended outcome could not be established from current evidence; it is not proof the side effect failed. A later fresh observation can be used for a separate reconciliation workflow.
- Existing task-level structured success criteria continue to produce `unknown` when evidence is missing or stale.

## Files changed
- `services/director/core.py`
- `tests/test_director_outcome_verification.py`
- `tests/test_phase23_verified_outcome_unknown.py`
