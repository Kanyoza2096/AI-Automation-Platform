# Phase 17.3 — Explicit Production Worker Bootstrap

## Purpose

Provide a dedicated, opt-in entry point for the durable engineering worker. The normal web application and module imports do not start workers. Production activation remains an operator-controlled deployment action.

## Activation contract

The dedicated worker process must set `KDO_ENGINEERING_WORKER_ENABLED=true` and provide:

- `KDO_ENGINEERING_DATABASE_URL`: PostgreSQL connection URL (never SQLite).
- `KDO_ENGINEERING_WORKER_ID`: unique worker identity.
- `KDO_ENGINEERING_TENANT_ID` and `KDO_ENGINEERING_WORKSPACE_ID`: explicit claim scope.
- `KDO_ENGINEERING_WORKER_ACTOR_ID`: governed workflow actor identity.
- Optional lease, heartbeat, poll, and backoff settings.

The host constructs the PostgreSQL connection factory, `PostgresEngineeringBuildQueue`, governed workflow, and bounded executor, then explicitly calls `run_production_engineering_worker`. The bootstrap checks PostgreSQL connectivity and confirms the queue/task/workspace schema exists before polling. SQLite queues are rejected; there is no fallback.

## Lifecycle and health

The bootstrap publishes `starting/running/stopped/failed` lifecycle snapshots through an optional health callback. Successful job results update the last-result timestamp and job identifier. Callback failures are logged and do not change job authority or execution outcome.

The caller owns process supervision and OS signal handling. It should set the supplied stop event on SIGTERM/SIGINT, allow the worker service to stop claiming, and provide a bounded process shutdown deadline. Worker execution still ends at the existing governed `awaiting_review` boundary; this bootstrap grants no merge, deployment, or release authority.

## Safety and limitations

- Disabled unless explicitly enabled in the dedicated worker process.
- PostgreSQL queue type, PostgreSQL URL, required schema, worker identity, tenant/workspace scope, and actor are validated before polling.
- Secrets are not logged or discovered by the bootstrap; the host owns secret injection and connection pooling policy.
- The bootstrap does not create migrations or alter the application's default SQLite configuration.
- A process/container executor with hard cancellation and resource/network/filesystem controls is still required for production-grade untrusted-code isolation.
- Uncertain-job reconciliation, human-authorized replay, real deployment topology validation, and operational alerting remain separate gates. This phase does not activate production execution.
