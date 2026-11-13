# Kanyoza Director — Backend Integration Matrix (source audit)

**Audit scope:** repository source in this archive. This is not live-runtime evidence.

## Director boot and wiring

| Concern | Source evidence | What is established | Gap / verification needed |
|---|---|---|---|
| Lifecycle startup | `lifecycle.py::_boot_director` | Initializes Director, attempts engine discovery, starts it, registers shutdown and health callbacks, and binds capabilities. | Live startup logs and deployed health snapshots are needed to prove successful wiring in deployment. |
| Engine discovery | `services/director/analysis.py::ENGINE_REGISTRY`; `services/director/core.py::auto_discover_engines` | Imports registered symbols and wraps discovered objects as `FunctionObserver`s. Only the explicit `ENGINE_FACTORY_GETTERS` allowlist is invoked; arbitrary registered request callables are not executed during discovery. Runtime discovery diagnostics expose discovered entries, missing exports, import failures, and factory failures in Director status. | Registry entries still require per-target instance/export contract checks; live runtime registration remains unverified. |
| Health evidence | `services/director/core.py::_make_observer`; `services/director/analysis.py::FunctionObserver` | Missing or unsupported health status remains UNKNOWN; stats-only responses are not treated as healthy. `get_status`/`health_check` payloads must explicitly report status (or `overall`). | Need runtime tests against actual exported engine objects and status contracts. |
| Observation provenance | `services/director/core.py::_snapshot_observations` | Snapshots now preserve observer source and observation timestamp alongside status, metrics, and issues. | Source authority and domain-specific freshness policies still need typed contracts. |
| Planning | `services/director/core.py::decide`, `_build_plan_async` | Rule-based decisions derive from health, observed issues, goals, and learning statistics. | Current plan output is a task list, not a durable mission graph with dependencies, deadlines, compensation, and replanning lineage. |
| Capability binding | `services/director_bindings.py::bind_director_capabilities` | Named handlers are bound during lifecycle startup; posting-schedule capability receives governed handoff only with a DB client. | Bound status does not prove production readiness; each handler's real dependencies and outcome contract require testing. |
| Manual execution API | `services/director/api.py::execute_capability_endpoint`; `services/director/core.py::execute_capability` | Direct dispatch refuses proposal-capable actions and capabilities requiring approval before handler invocation. | Test all alternate callers and confirm no bypasses in deployed wiring. |
| Governed schedule action | `services/director_bindings.py::_make_brand_schedule_governed_handoff`; `services/sector_brand_schedule_execution.py` | Approved proposal handoff uses Action Kernel with workspace, decision, correlation, idempotency, and action contract information. | Production DB durability, concurrent retries, and deployed provider behavior remain unverified. |
| Outcome verification | `services/director/core.py::_authoritative_resource_state`, `_verify_schedule_change`, `_verify_worker_scale` | Verification requires a healthy/accepted observer, parseable timestamp no older than five minutes, and matching resource data under `metrics.brands` or `metrics.worker_pools`. | The current observer registry does not prove these domain collections are populated. Real domain-state observer wiring is still required. |
| Worker scaling | `services/director_bindings.py` handler mapping; Director capability definition | Scaling decision and intent paths exist. | The current handler records intent; it does not prove a real worker-pool resize. Verification remains false without authoritative worker-count observation. |
| Durable task storage | `services/director/stores.py::AsyncSupabaseStore` and `InMemoryStore` | Supabase-backed and process-local stores share a method surface. | Need staging tests for schema, persistence across restart, DB errors, and concurrent updates. |
| Learning | `services/director/analysis.py::LearningLoop`; `services/director/core.py` outcome paths | Learning signals and outcome statistics are tracked. | Demonstrate that only verified, provenance-backed outcomes become learning evidence. |

## Registered observation targets

`ENGINE_REGISTRY` names runtime-owned targets for content, rendering, conditions, email, engagement, Guardian, Meta Engine, AI Orchestrator, the canonical `brand_scheduler`, job stats, plugin registry, event bus, render queue, image generation, prompt building, and health monitoring. Brand-scoped topic selection is an internal content-workflow component, not a platform singleton or independent Director observation target; its operational evidence belongs to the content workflow. Registration means an observer is attempted; it does not prove health or decision integration. Function/class exports and singleton symbols need per-target contract checks.

## Current source-level changes relevant to this matrix

1. Engine observers preserve UNKNOWN when no explicit health status is reported.
2. Engine discovery invokes only explicit supported factory getters rather than arbitrary callables.
3. Manual capability execution enforces approval and proposal boundaries.
4. Observation snapshots include source and timestamp.
5. Schedule and worker-scale predicates fail closed unless a fresh, accepted-status observer reports the requested resource state in the defined metrics contract.

## Next engineering steps

1. Implement typed evidence/organism-state contracts with provenance, source authority, freshness, and explicit unknown/conflict states.
2. Wire authoritative brand and worker-pool domain observers to the post-action verification contract.
3. Build a decision context that consumes actual specialized engine outputs and explains evidence behind diagnoses.
4. Add durable mission/step/dependency state only after confirming persistence schema and preserving existing task workflows.
5. Exercise DB persistence, workspace isolation, duplicate delivery, restart recovery, and actual provider actions in staging.

## Verification record

- Latest focused combined run: **79 passed** across outcome-verification, Director auth/catalog, engine/health truth, Action Kernel, and business handoff tests.
- Separate Director-focused run: **43 passed** across `tests/test_director*.py` and selected Action Kernel tests. These runs overlap and must not be added together.
- Python compilation passed for `services/director/core.py` and `tests/test_director_outcome_verification.py`.
- Full repository suite, live runtime, production DB durability, and deployed side effects are **not verified**.


## 2026-10-20 continuation — observer trust and discovery safety

- Fixed `_make_observer` so a status payload missing `status`/`overall` remains `unknown`; it no longer defaults to healthy. Stats-only engines are also `unknown` rather than healthy.
- Restricted discovery-time invocation to the explicit `ENGINE_FACTORY_GETTERS` allowlist (`get_job_stats_service`, `get_render_queue`). Other callable registry exports, including request handlers, are registered as objects without being invoked during discovery.
- Added `tests/test_director_observer_contracts.py` for missing status fields, stats-only contracts, explicit degraded status, non-invocation of arbitrary callables, and visibility of missing exports/import failures in discovery diagnostics.
- Focused regression command passed **116 tests** across Director tests, engine-truth tests, Action Kernel, business action handoff, and connector-health control.
- This reduces false-health and discovery-side-effect risk at source level; live instance wiring and deployed behavior remain unverified.

- Director `status().diagnostics.engine_discovery` now carries the last discovery report and counts, so unresolved registry entries are visible instead of silently skipped.

## 2026-10-20 continuation — callable export / live-instance contract

A source-checkout discovery run against the current registry found 8 discovered object/factory observers out of 18 registry entries, 3 missing exports, and 7 class/function exports that are not live instances. No import or configured-factory failures occurred in that run. This is a local source-checkout result, not a live deployment snapshot.

The registry previously wrapped any non-factory callable (including classes and request handlers) as an observer. That could make a callable look like an engine registration despite there being no instance to observe. Discovery now refuses to register these unsupported exports and reports them under `unsupported_exports` with `live_instance_or_explicit_adapter_required`. It does not instantiate arbitrary classes or execute request functions. The configured zero-argument factory allowlist remains unchanged.

Validation: `pytest -q tests/test_director*.py` passed **83 tests**; `py_compile` passed for the changed core module and observer-contract tests. The smaller test count reflects this command's scope, not a regression from earlier broader test selections. The registry's missing exports and unsupported class/function targets remain integration work, and the full repository and deployed runtime remain unverified.
