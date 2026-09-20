# KDO Phase 1 — Authoritative Execution Spine Report

Release: 2.18.0
Status: LOCAL VALIDATION PASS
Date: 2026-08-30

## Objective

Collapse publication execution onto one governed path and remove scheduler/direct-plugin paths that could bypass the Decision Gate and durable outbox.

## Canonical production path

Trigger → PostPublishWorkflow → Guardian → Metabolism → Council → DecisionGate → durable PublishOutbox → atomic PostgreSQL claim → worker → connector → verified outcome → AutomationJob reconciliation → event/audit.

## Changes

### 1. Scheduler convergence
- Brand scheduler now resolves `PostPublishWorkflowFactory` rather than the legacy `ContentGenerationPipelineFactory` for scheduled/manual content publishing.
- PostScheduler no longer falls back to direct `PluginRegistry.publish_all`; if the canonical workflow is unavailable it fails closed.
- Legacy direct-publish helper remains only as a compatibility shim that returns a controlled error.

### 2. Job lifecycle bridge
- Added async JobService lifecycle operations: `reset_to_pending`, `mark_published`, `mark_failed`, `get_today_stats`, and `recover_and_retry`.
- Legacy synchronous scheduler threads use a safe async bridge rather than accidentally treating coroutines as job objects.
- Worker publication reconciles jobs only after a connector result is verified.

### 3. Durable outbox
- Outbox rows now carry `automation_job_id`.
- Production outbox persistence remains fail-closed.
- Production claims are database-first and use PostgreSQL `FOR UPDATE SKIP LOCKED` through `claim_publish_outbox`.
- Processing leases are stored with `locked_until` / `locked_by` so a worker crash can make work claimable again.
- Successful and failed processing clears the lease.

### 4. Media continuity
- Canonical enqueue persists URL-backed media references.
- Byte-backed media can be persisted as base64 payload for the current phase so a process restart does not silently discard rendered content. This is intentionally a transitional mechanism; a dedicated object/media store is recommended before very large media workloads.
- Worker execution reconstructs persisted media before calling the connector.

### 5. Workflow truthfulness
- `PostPublishWorkflow.PublishStep` delegates to the canonical publish orchestration rather than publishing directly through the plugin registry.
- A durable enqueue is represented as `queued`, not `published`.
- Analytics/event stages do not emit a false PostPublished event for a merely queued outbox item.

### 6. Database migration
- Added `migrations/021_phase1_publish_spine.sql` with job correlation, leases, index, and atomic claim function.

## Validation performed

- Python compilation: PASS.
- Existing regression/contract suite: `34 passed`.
- Scheduler direct `publish_all` / `publish_to` bypass scan: PASS (no occurrences in scheduler/workflow code).
- Canonical publish call scan: PASS.
- Factory wiring scan: PASS.

## Remaining production gates

1. Apply migration 021 to the actual production database and execute a two-worker claim race test.
2. Run a real connector integration test with platform credentials in a controlled environment.
3. Verify lease recovery by killing a worker during processing.
4. Replace base64 media payloads with object storage/media artifact persistence for large assets.
5. Complete persistent DecisionGate state and distributed idempotency/quota controls in Phase 2.
6. Run clean-container dependency installation and full application boot against real infrastructure.

## Important non-claim

This release is locally validated, not production-certified. External Supabase/Postgres, Redis, social-platform credentials, and third-party APIs were not available in the sandbox and therefore cannot be represented as passing live integrations.
