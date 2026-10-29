# Kanyoza Director — P3 lifecycle-owned engine registration

Date: 2026-10-20

## Finding

`services/meta_engine/core.py` previously resolved arbitrary class and callable exports by invoking them during engine registration. That behavior could instantiate classes without lifecycle ownership or execute request-handler functions as though they were engine factories. It also obscured which entries were actual live instances.

## Changes

- `MetaEngineV7._resolve_engine_instance()` now accepts live objects only and rejects classes/callables. Async factories are explicitly identified as requiring lifecycle initialization.
- `register_all_engines()` resolves only a small explicit allowlist of known singleton getters (`get_job_stats_service`, `get_render_queue`, `get_event_bus`). All other class/callable exports are skipped, not invoked.
- Added `_registration_report` with `registered` and `skipped` lists and bounded reasons, including missing exports and unsupported callable/class exports.
- Plugin registrations are also recorded; invalid plugin values are skipped rather than silently treated as registered.
- Added regression coverage proving arbitrary callables are not invoked and skipped exports are visible.

## Validation

- `python -m compileall -q services/meta_engine/core.py tests/test_engine_truth.py`: passed.
- `pytest -q tests/test_engine_truth.py tests/test_director_observer_contracts.py`: 22 passed.
- `pytest -q tests/test_director*.py tests/test_engine_truth.py tests/test_director_observer_contracts.py`: 98 passed.

## Limits / next work

This is source-level validation. It does not certify live production container wiring or database durability. Some registry entries will now be explicitly skipped until their owning service container provides real instances or a reviewed adapter. Next: build a lifecycle/container adapter for the concrete engines, map dependencies to the canonical topology, and test startup/shutdown and observer health contracts without fabricating status.
