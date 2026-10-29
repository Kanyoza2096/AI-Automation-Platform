# Phase 1 — Authoritative Publish Execution Spine

Status: **IMPLEMENTED — LOCAL VALIDATION PASS**

The scheduler/manual post workflow now routes publication through the canonical governance pipeline. Durable publication carries its originating automation job ID into the outbox, and the production outbox claims work atomically from Postgres with `FOR UPDATE SKIP LOCKED`. Production enqueue fails closed when persistence is unavailable. Worker completion reconciles the AutomationJob only after a real connector outcome.

## Canonical path

`trigger → PostPublishWorkflow → Guardian → Metabolism → Council → DecisionGate → durable outbox → atomic worker claim → connector → verified outcome → AutomationJob → event/audit`

## Remaining validation

- Run full Python tests and compilation.
- Static scan for direct scheduler `publish_all` bypasses.
- Validate all factory wiring imports.
- Validate migration syntax structurally.
- Real Supabase/Postgres integration remains required before production certification.


Direct scheduler/plugin publication bypasses are fail-closed when the canonical workflow is unavailable. The legacy ContentGenerationPipeline remains registered only for compatibility and is no longer selected by the production scheduler.
