# Kanyoza P15 — Director Signal Coverage in Health Reporting

## Objective
Connect the existing Director engine-signal contract to the daily system-health response without inventing health, triggering additional engine probes, or merging independent evidence sources.

## Changes
- Added `supervisor_evidence.director_signal_contract` to the `/api/v1/system-health` response.
- The report reads existing Director observations and surfaces contract coverage, observer coverage entries, accepted/rejected recommendation counts, and rejection reasons.
- Signal-contract reporting is explicitly separate from Meta Engine health, Director organism state, and service-registry evidence.
- HealthChecker aggregation now requires explicit boolean `ok` values. Missing/ambiguous results are `unknown` or `partial`; they no longer silently default to healthy.
- Added regression tests for missing explicit health results, mixed known/unknown results, and Director signal coverage visibility.

## Validation
- `python -m compileall -q routes/backend_contract_routes.py tests/test_phase3714_health_truth.py` — passed.
- `pytest -q tests/test_phase3714_health_truth.py tests/test_engine_truth.py tests/test_director_engine_signals.py tests/test_director_observer_contracts.py` — 58 passed.
- `pytest -q tests/test_director*.py tests/test_engine_truth.py tests/test_phase3714_health_truth.py tests/test_backend_route_contract.py` — 124 passed.
- Archive integrity check — performed on packaged output.

## Boundaries
This is source-level validation, not a live deployment test. It does not create a production signal publisher for engines that do not yet emit `metrics.director_signals/v1`; instead it makes coverage gaps visible. The full repository suite has not been established green, and prior runs identified a missing `supabase` package in the publishing test environment.

## Next step
Trace real engine publishers and implement the contract at the owning engine boundaries, beginning with the services that appear unhealthy in daily reports (`job_service`, `meta_engine`, `autonomous_content`). Add contract conformance tests per publisher before using their recommendations for governed Director planning.
