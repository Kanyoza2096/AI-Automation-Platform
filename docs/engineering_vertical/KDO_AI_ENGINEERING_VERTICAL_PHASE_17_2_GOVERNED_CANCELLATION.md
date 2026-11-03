# KDO AI Software Engineering — Phase 17.2 Governed Execution Cancellation

## Objective

Connect worker lease loss and graceful worker shutdown to the opt-in Docker QA runner, so an active test container is stopped instead of continuing after its execution authority is no longer valid.

## Execution path

1. The durable worker receives the process shutdown event.
2. The worker service passes that same cancellation event through the lease-heartbeat supervisor.
3. The governed queue execution sets cancellation when the lease renewal fails, the lease fence no longer matches, or the maximum execution deadline is reached.
4. The Phase 16.2 workflow passes the cancellation event only to executors that explicitly accept a `cancellation_event` keyword. Existing executor callbacks remain backward compatible.
5. The Phase 16.7 dynamic quality executor forwards cancellation to cooperative build executors, the quality loop, and the Phase 16.13 containerized test runner.
6. The Docker runner watches cancellation while the Docker CLI is running. It force-removes the generated container and reports `cancelled` only after cleanup is confirmed. Cleanup failure raises a fail-closed error.
7. The quality loop stops before diagnosis/repair/verification when cancellation is already active, and does not apply a patch after observing cancellation.

## Safety boundaries

- Queue lease fencing remains authoritative; cancellation is not a substitute for checking the current lease.
- Worker shutdown prevents new claims and now signals the active job.
- A lost lease or execution deadline signals cancellation and preserves the existing uncertain-job reconciliation path.
- Cancellation is cooperative for build/agent adapters. An adapter that does not accept `cancellation_event` cannot be forcibly interrupted by Python; it must use bounded downstream calls and check cancellation itself.
- Docker test execution is terminated by removing the daemon-side container. If removal cannot be confirmed, the runner fails closed.
- The runner remains opt-in. This change does not activate a production worker or alter database selection.
- Patch application remains policy-authorized; independent review remains required; no merge, deployment, or release authority is granted.

## Validation added

- Container tests cover cancellation before launch and cancellation during a running container, including daemon cleanup.
- Worker runtime tests verify that the shutdown event is forwarded to the governed execution layer.
- Dynamic QA tests verify that an already-cancelled task does not start tests or apply patches.
- Containerized pipeline tests verify cancellation-event propagation.
- The focused engineering CI workflow now explicitly runs the Phase 16.7, 16.12, and 16.13 test modules.

## Remaining production work

- Run the full CI workflow and validate with an actual Docker daemon (unit tests use a controlled process runner).
- Provide a cancellation-aware production agent/build adapter and hard bounds for model/tool calls.
- Validate rootless Docker or an equivalent hardened runtime, daemon access isolation, workspace mount policy, and resource enforcement in staging.
- Complete explicit worker bootstrap, uncertain-job reconciliation, staging multi-worker validation, and observability before enabling production execution.
