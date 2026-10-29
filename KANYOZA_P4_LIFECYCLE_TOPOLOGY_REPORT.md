# Kanyoza P4 — Lifecycle and Topology Integration Report

## Scope

P4 builds on P3's safe engine registration rules. The objective is to make the lifecycle's actual service instances, Meta Engine's monitored instances, and the canonical topology easier to compare without inventing health or invoking arbitrary factories.

## Changes

- `core/service_registry.py`
  - Rejects classes, routines, callable exports, and awaitables as registered service instances. Awaitables are closed when possible before raising, avoiding un-awaited coroutine leakage.
  - Exposes a defensive-copy registration report.
  - Exposes a topology report showing registered runtime names, Meta Engine names, names missing from Meta Engine, instance-identity mismatches, canonical graph drift, and an explicit `health_evaluated: false` marker.
- `core/service_definitions.py`
  - Captures successful registrations, optional skips, critical failures, and Meta Engine registration failures with stable reason codes while retaining the existing integer return contract.
- `routes/backend_contract_routes.py`
  - The `/topology` response now includes the registry's topology and registration reports.
  - An async health-probe exception is reported as `error`, not `healthy`.
  - A non-empty probe result without an explicit status no longer automatically implies healthy.
  - A missing/unknown health contract remains `unknown` rather than being fabricated as healthy.
- `tests/test_engine_truth.py`
  - Adds tests for rejecting factories/awaitables, truthful topology reports, defensive copies, instance-identity drift, and registration outcomes.

## Validation

- `compileall` for changed Python modules and tests: passed.
- Focused Director / topology / route / health-truth suites: **107 passed**.
- Full-suite isolation with `pytest -x -q`: **655 passed before the first failure**. The first failure is `tests/pipeline/test_publish_end_to_end_contract.py::test_connector_receives_brand_for_tenant_specific_facebook_credentials`; the runtime reports `No module named 'supabase'` and refuses to proceed with an in-memory ledger. This failure is in the publishing pipeline dependency/environment, not the P4 registry/topology changes. The full suite was not completed to green and must not be represented as passing.

## Important interpretation

Registration and topology are not health. The registry report deliberately says `health_evaluated: false`; the `/topology` endpoint reports explicit probe status separately. `topology_complete` may remain false when canonical nodes are not actually present. That is a useful discovery signal, not a reason to manufacture placeholder services.

## Remaining work

- Wire missing canonical services from their owning lifecycle/container rather than widening the arbitrary getter allowlist.
- Add real `supabase` dependency/environment to the publishing test environment and rerun the complete suite.
- Verify startup in a live deployment and compare actual `/topology` output against the daily health report before claiming production readiness.
