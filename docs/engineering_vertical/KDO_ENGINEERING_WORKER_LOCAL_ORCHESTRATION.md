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


## Existing KDO gateway adapters

The worker composition now has a concrete adapter path through KDO's existing
`LLMGateway.complete_structured` and versioned `EngineeringPromptAssembler`.
A trusted host factory can call
`build_gateway_worker_dependencies(...)` from
`services.kdo_engineering_worker_composition` instead of inventing a second
model-provider integration.

The gateway-backed builder receives the approved task contract, approved plan,
and a bounded text-only snapshot of the task worktree. It can propose one
unified diff. The host rejects unsafe paths and symlink targets, checks the
task-scoped workspace, and calls the injected host policy callback
for `build_patch` before applying the patch through the checked Git patch
primitive. Important: this callback is currently an interface seam, not a
verified Action Kernel integration. KDO still needs an engineering-specific
canonical decision/Action Kernel adapter; do not wire a permissive lambda or
claim this authority path is complete. This is a host filesystem mutation, not a sandbox; it is only valid
inside the disposable task worktree.

The dynamic QA registry installs task-aware debugging, backend repair, and
independent-review adapters from the existing role catalogue. The backend
adapter may propose a repair diff, but the quality loop still authorizes patch
application and executes tests only through the digest-pinned Docker runner.
The model reviewer cannot create a durable human review ID or authorize release.
It may provide an AI quality-verification result tied to the observed gateway
request ID after inspecting machine test evidence; this only allows the workflow
to hand the workspace to the separate human code-review stage. The human review
decision remains pending and must be recorded independently.

The adapter integration is covered by
`test_gateway_agents.py` and `test_gateway_build_executor.py`. A passing CI
run is still required before merge. No worker is enabled by this change, and
no live calculator build has been executed.


## Remaining authority integration blocker

The gateway adapter and worker composition intentionally accept host-owned
authorization callbacks, but the repository does not yet provide a verified
engineering-specific Action Kernel contract that maps plan approval, build
patching, test execution, repair application, and review handoff to canonical
decision IDs and durable receipts. The existing generic Action Kernel must not
be invoked through an unrelated business action or a fabricated decision ID.
Until those worker-side adapters are implemented and tested against the
canonical Council / DecisionGate / Action Kernel path, the worker must remain
disabled. The passing
focused CI suite proves adapter interfaces and unit behavior only; it does not
prove the live authority chain or authorize staging execution.
