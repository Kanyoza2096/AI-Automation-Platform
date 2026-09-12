# Phase 0 Runtime Audit — 2026-08-29

## Verification performed

### Static/runtime checks
- Python compilation of all modified Phase 0 modules: **PASS**
- Test suite with project root on import path: **34 passed**
- `SERVICE_MODE=workers python -m workers.scheduler_worker`: **booted successfully** in the available sandbox with development infrastructure fallbacks.
- `SERVICE_MODE=core python -m workers.brain`: **booted successfully** far enough to initialize EventBus, TaskQueue, AI, Meta, Director, Condition Engine and Autonomous Loop. External Gemini calls were naturally unavailable because the sandbox has no external network.

### Findings exposed by boot testing and fixed
- Missing worker entrypoint modules.
- `SERVICE_MODE=workers` did not boot TaskQueue.
- FastAPI lifespan did not await async lifecycle functions.
- Health registry was awaited incorrectly in health endpoints.
- Scheduler stale-job recovery called an async JobService method synchronously.
- Scheduler was wired to the wrong workflow factory.
- Post-publish factory was silently aliased to a different pipeline implementation.
- Production Socket.IO query-string token fallback.
- Production publish simulation defaulted to enabled.
- Root workspace enumeration endpoint remained present despite the Phase 0 status document claiming it was removed.
- Trend/backup route imports did not match the actual filesystem layout.
- SLO/outbox cancellation handling could leak `CancelledError`.
- Autonomous Content Engine referenced an undefined `ContentMetrics` type.
- Meta initialization invoked an async stop method without awaiting it.

## Not certified yet

This audit does not certify production because the sandbox cannot install missing external dependencies without network access and does not contain the user's real Supabase/Redis/LLM credentials. Production CI must still execute a clean dependency install, application import, container boot, health checks and end-to-end service tests.
