# Kanyoza P16 — Director Signal Publisher Contract

## Objective
Extend the P15 signal-coverage work by making the first three priority engines expose a consistent, side-effect-free `metrics.director_signals/v1` envelope: `job_service`, `meta_engine`, and `autonomous_content`.

## Changes
- Added `core/director_signal_contract.py`, a shared contract helper. It validates that signal payloads are lists and returns the standard `director_signals` plus contract identifier.
- `JobService.health_check()` now carries the contract in its metrics while preserving unknown health results as unknown, including when its health check raises.
- `MetaEngineV7.check_health()` and `check_health_async()` now expose the same metrics envelope across starting, observed, and exception paths. Existing health status semantics are preserved.
- `AutonomousContentEngine.check_health()` now reports the actual `_running` flag and publishes the same metrics envelope.
- Added `tests/test_phase16_director_signal_publishers.py` for contract validation, health-state preservation, and publisher conformance.

## Important safety and truthfulness boundary
These publishers currently emit an **explicit empty recommendation list**. This means no recommendation is published by these engines yet; it does not imply the engines are healthy. No recommendations were fabricated from missing telemetry, and no action was executed. The next phase must add evidence-backed recommendation generation and verify that these engine observations are actually ingested by the running Director observer set, with sector/capability checks and existing governance intact.

## Validation
- Python compilation of the changed modules and new test: passed.
- Focused combined suite:
  `pytest -q tests/test_director*.py tests/test_engine_truth.py tests/test_phase3714_health_truth.py tests/test_backend_route_contract.py tests/test_phase16_director_signal_publishers.py`
  **129 passed**.
- Live deployment, external Supabase connectivity, and production runtime behavior were not tested in this environment.
- Full repository test suite was not claimed as passing.
