# Kanyoza P22 — Director Runtime Binding and Execution Audit

## Objective

Connect the Director's static capability readiness catalog to the actual binding report produced during lifecycle startup, and ensure that handler stubs do not report intent or cache manipulation as successful execution.

## Implemented

### 1. Runtime binding evidence is now visible

`bind_director_capabilities()` now stores its exact per-capability binding statuses and UTC report timestamp on the Director instance. `capability_catalog()` exposes:

- `binding_status` and `binding_reported_at`;
- `runtime_binding_confirmed`, which is true only for an explicit `bound` or `bound_governed_handoff` result;
- `runtime_dependencies_verified`, explicitly false until a real dependency probe exists;
- `readiness_scope: static_wiring_and_contracts_only`.

The authenticated `GET /capabilities` summary now includes whether a binding report exists, its timestamp, confirmed binding counts, unconfirmed binding counts, and verified runtime-dependency count. The endpoint remains observational and does not execute capabilities.

### 2. Worker scaling no longer returns false success

`scale_worker_pool` had no actual worker-pool scaling adapter but returned `ok: true` after merely recording an intent. It now returns `status: unavailable` with `worker_scaling_adapter_not_bound` and `intent_not_executed`. A proposal cannot mistake logging an intent for a completed resize.

### 3. Restart fallback no longer reports a restart that did not happen

`restart_failed_service` now returns an unavailable result when no supported recovery method exists. Clearing or manipulating an observation cache is not a restart and is not accepted as proof of recovery. A supported recovery method's result is still returned through the existing handler path.

### 4. Regression coverage

Added `tests/test_phase22_director_runtime_binding.py` for binding-report propagation, the explicit unverified-dependency status, no-report behavior before lifecycle binding, and honest failure when worker scaling has no adapter.

## Validation

- Focused Director and health regression suite: **145 passed**.
- Python compilation: passed for `services/director`, `services/director_bindings.py`, and the P22 test.
- Archive integrity: checked after packaging.
- A broader publishing-contract selection also produced four failures outside the P22 changes: two require a Supabase client/package absent in this environment, and two expect `publish_scheduled_content` in the Director handler map even though the current binding module intentionally excludes it to prevent duplicate autonomous publishing. These failures are documented rather than hidden or represented as P22 regressions.

## Limits

This report does not claim that production services, Supabase credentials, worker scaling, provider connectors, or recovery APIs are live. Binding a callable is not equivalent to verifying its downstream dependencies or its side effects. Runtime dependency probes and authoritative post-action observations remain separate requirements.

## Files changed

- `services/director/core.py`
- `services/director/api.py`
- `services/director_bindings.py`
- `tests/test_phase22_director_runtime_binding.py`
