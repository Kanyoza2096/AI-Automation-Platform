# Kanyoza P19 — Meta Engine Evidence-backed Signals

## Objective
Extend P18's evidence-backed job backlog recommendation with a safe Meta Engine signal grounded in its own explicit system snapshot.

## Changes
- Added a Meta Engine signal builder that only acts on a non-null snapshot with nominal overall status.
- When the snapshot explicitly marks one or more engine records as `EngineStatus.STALE`, publishes a single advisory using the registered `run_health_scan` capability in the health sector.
- The advisory names stale engines, uses bounded evidence (up to ten names in the rationale), and asks for inspection rather than asserting failure or attempting recovery.
- Degraded, unknown, absent, or malformed snapshots do not generate this recommendation.
- Both synchronous and asynchronous Meta Engine health paths use the same signal builder.
- Added regression tests for evidence gating and Director capability/sector validation.

## Safety boundaries
- `STALE` is not reinterpreted as `OFFLINE` or `FAILED`.
- The signal is advisory only; Director policy remains responsible for task governance.
- This phase does not create new autonomous-content recommendations because the existing data does not yet establish a reliable scheduling failure condition.

## Validation
- Python compilation: passed.
- Focused regression suite: see phase run output.
- ZIP integrity: checked after packaging.
- Production runtime and Supabase connectivity are not verified in this environment.
