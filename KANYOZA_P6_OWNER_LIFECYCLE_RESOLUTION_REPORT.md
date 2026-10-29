# Kanyoza P6 — Owner-Lifecycle Service Resolution

## Finding

`ServiceContainer.try_resolve()` is a lazy constructor: calling it can run a registered factory and create a service. The end-of-boot service registry runs even in restricted `SERVICE_MODE` configurations. Consequently, registration-only code could instantiate services whose owning boot steps had intentionally been skipped. Two additional resolver paths could start workers or produce a coroutine during collection: `get_render_queue()` starts render workers, and `get_unified_bus()` is async.

## Changes

- `service_container.py`
  - Added `try_get_instance(interface)`, which returns only an already-created singleton and never invokes its factory.
- `core/service_definitions.py`
  - JobService, TaskQueue, AutonomousLoop, AIOrchestrator, PluginRegistry, BrandSchedulerManager, EventRecorder, and HealthMonitorService use the instance-only API where applicable.
  - Event bus resolution now reads an existing lifecycle-owned `UnifiedEventBus`; it no longer falls back to returning a coroutine from the async getter.
  - JobStatsService resolution reads the owner-initialized module singleton rather than creating one before its boot step.
  - RenderQueue resolution uses a non-creating accessor.
  - Added optional registrations for already-existing canonical runtime nodes: `ai_orchestrator`, `plugin_registry`, `scheduler`, `event_recorder`, `health_monitor`, `email_service`, and `engagement_tracker`. Optional services that have not been initialized remain skipped.
- `core/render_queue.py`
  - Added `get_render_queue_if_initialized()` to observe an existing queue without starting worker threads.
- Tests verify that instance-only lookups do not invoke factories and registry collection does not create the event bus, JobStatsService, or RenderQueue.

## Validation

- Compilation of changed modules and tests: passed.
- Focused Director / topology / route / health-truth suites: **111 passed**.
- The full repository suite remains uncertified: a previous `pytest -x -q` run reached 655 passes and then failed because the publishing contract test's environment lacks the `supabase` package. The full suite must be rerun in an environment with the declared dependency installed.

## Important limits

- Optional nodes may still be absent when their owning boot step did not run; that is intentional and is now reported as a skip instead of triggering lazy construction.
- This is static/source-level validation. No live production deployment was connected or probed.
