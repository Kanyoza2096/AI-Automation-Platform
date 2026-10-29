# Phase 16.3 Validation Report

## Targeted tests

`pytest -q tests/engineering_vertical/test_phase16_3_worker_queue.py`

Actual local result: 11 passed.

Coverage includes queue idempotency and conflict rejection, lease fencing, conservative expired-lease reconciliation, tenant scoping, persistence across repository reopen, explicit high-risk Action Kernel contract registration, missing-decision denial, duplicate registration rejection, Council/Action Kernel admission with an injected authoritative receipt verifier, and claimed-worker execution stopping at `awaiting_review`.

## Broader regression results

- `pytest -q tests/engineering_vertical` — **143 passed**.
- `pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py` — **155 passed**.
- The full repository run `pytest -q --maxfail=1` stopped at **346 passed, 1 failed** on the existing unrelated architecture test `tests/architecture/test_phase5_business_onboarding_boundary.py::test_route_is_transport_adapter_not_onboarding_kernel_owner` still fails because it asserts the route source must not contain `get_service_client_from_settings`, which appears in that route's implementation/docstring. No Phase 16.3 file modifies that route.

## Static checks

`python -m py_compile services/kdo_engineering_phase16_3.py services/kdo_engineering_persisted_workflow.py tests/engineering_vertical/test_phase16_3_worker_queue.py` — **passed**.

## Not tested here

No real Council service, production Action Kernel database, canonical ledger RPC, multi-process/multi-host worker deployment, staging environment, OS sandbox, provider credentials, or production release path was exercised.
