# KDO Engineering Worker — Local Honcho Orchestration

## Purpose

`Procfile.dev` is a local-development process file. The root `Procfile` remains the Render web-service command and is intentionally unchanged. Install local dependencies with `pip install -r requirements-dev.txt`, then run `honcho start -f Procfile.dev`.

The `engineering-worker` process is fail-closed and disabled by default. With `KDO_ENGINEERING_WORKER_ENABLED` unset or false, it exits without connecting to PostgreSQL or claiming jobs. This lets developers run the API without accidentally starting a worker.

## Explicit worker activation

To activate the local worker, configure the existing `ProductionWorkerConfig` variables in the worker process environment and set:

- `KDO_ENGINEERING_WORKER_ENABLED=true`
- `KDO_ENGINEERING_WORKER_FACTORY=package.module:build_worker_dependencies` (a trusted host wrapper, not the composition function directly)
- `KDO_ENGINEERING_DATABASE_URL` to a non-production PostgreSQL database
- `KDO_ENGINEERING_WORKER_ID`, `KDO_ENGINEERING_TENANT_ID`, `KDO_ENGINEERING_WORKSPACE_ID`, and `KDO_ENGINEERING_WORKER_ACTOR_ID`

The factory is operator-owned code and is called with `config` and a psycopg connection factory. It must construct the trusted Action Kernel-backed workflow and pipeline authorization callbacks, a task-aware agent registry, and a real build adapter, then call `services.kdo_engineering_worker_composition.build_worker_dependencies(...)`. That composition function wires the PostgreSQL queue, PostgreSQL task/workspace workflow, and digest-pinned Docker QA/repair executor. It returns a mapping containing a `PostgresEngineeringBuildQueue`, governed `workflow`, and bounded `executor`; optional `health_sink` and `cleanup` callbacks are supported. Do not pass the composition function directly as the environment factory: the policy and agent adapters must be supplied by trusted host code. No SQLite fallback is accepted. The entrypoint handles SIGTERM/SIGINT by setting the worker stop event, which prevents new claims and is forwarded as the executor cancellation event.

`KDO_ENGINEERING_WORKER_MAX_JOBS` can be set to a positive integer for a finite local smoke run. Omit it for continuous polling.

## Safety boundary

This is process orchestration, not a sandbox. Honcho does not isolate child processes, constrain resources, block network access, or guarantee hard cancellation. Do not point this worker at production. Do not enable generated-code execution until the injected executor runs inside the approved isolated-container design with digest pinning, hard timeout/cancellation, resource limits, no network, read-only root filesystem, non-root identity, dropped capabilities, task-scoped writable storage, and host-level supervision. The existing acceptance gate remains authoritative.

No Render service, Supabase project, production Procfile, or deployment setting is changed by this local setup.
