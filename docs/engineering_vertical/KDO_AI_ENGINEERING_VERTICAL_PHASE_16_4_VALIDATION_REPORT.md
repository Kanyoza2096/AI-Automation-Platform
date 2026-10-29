# KDO Phase 16.4 Validation Report

## Exact results

| Command | Result |
|---|---:|
| `pytest -q tests/engineering_vertical/test_phase16_4_shared_queue.py` | 7 passed |
| `pytest -q tests/engineering_vertical` | 150 passed |
| `pytest -q tests/engineering_vertical tests/reliability/test_execution_ledger.py tests/test_condition_engine_council_approval_fence.py` | 162 passed |
| `pytest -q --maxfail=1` | 346 passed, 1 failed; stopped at first failure |
| Python compilation of Phase 16.4 module and test | Passed |
| ZIP integrity (`unzip -t`) | Passed |

## Existing full-suite failure

`tests/architecture/test_phase5_business_onboarding_boundary.py::test_route_is_transport_adapter_not_onboarding_kernel_owner` fails because the existing route source contains `get_service_client_from_settings`, including in its docstring and implementation. This failure is outside the Phase 16.4 changed files. The test run stopped at that first failure; the complete repository suite is not certified green.

## PostgreSQL validation limitation

No `psycopg` or `psycopg2` package was installed, and no test PostgreSQL DSN was supplied. Adapter tests use a scripted DB-API fake to validate SQL contract shape, fail-closed fencing, and transaction behavior. They do **not** prove live PostgreSQL syntax acceptance, multi-process locking, migration application, RLS/grant behavior, or failover. Those require a dedicated disposable PostgreSQL/Supabase staging database and must be run before production use.
