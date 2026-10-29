# Kanyoza P14 — Runtime Presence vs Health Reconciliation

## Finding

The canonical `/api/v1/topology` route could infer `healthy` from presence in the runtime-name set when no explicit health result existed. It also built that set from health-status dictionary keys, so a failed probe could make a missing engine appear registered. These semantics blurred three different facts: instance presence, probe outcome, and health status.

## Change

- Runtime presence now comes from actual service-registry membership or a concrete singleton object found by the route's explicit probe path. A status/error entry alone does not establish an instance.
- Missing health evidence now remains `unknown`; a probe exception remains `error`; registration alone never yields `healthy`.
- Added `_topology_status` to normalize only explicit source-reported states (`nominal`/`running` to `healthy`, `stopped` to `degraded`) while preserving `unknown` and `error`.
- Added regression coverage for registered-but-unprobed, probe-error, explicit nominal, and stopped states.
- Includes P13's engine-signal governance guard and end-to-end governed-handoff tests.

## Validation

- `pytest -q tests/test_director*.py tests/test_engine_truth.py`: 117 passed.
- Compilation of modified route and test modules: passed.
- ZIP integrity: passed.

## Boundaries

This does not claim production health or a live startup test. Runtime presence is still limited to registry membership and the singleton probes explicitly implemented in the endpoint. The full repository suite still has a known environment limitation around the missing `supabase` dependency documented in earlier phase reports.
