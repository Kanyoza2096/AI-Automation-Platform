# KDO AI Software Engineering — Phase 17.1 Container Timeout Hardening

## Objective

Close a lifecycle gap in the existing opt-in Docker test runner: a timeout of the local Docker CLI process does not prove that the Docker daemon stopped the container.

## Change

- Assign every test container a unique, generated name.
- Preserve the existing digest-pinned image requirement, fixed allowlisted test profiles, no-network mode, read-only container root, dropped capabilities, non-root UID, and CPU/memory/PID/time/output bounds.
- When the Docker CLI exceeds its execution deadline, explicitly issue `docker rm --force <generated-name>` to stop and remove the daemon-side container.
- Treat inability to confirm cleanup as a hard failure (`container_timeout_cleanup_failed`) rather than reporting an ordinary timeout as safely contained.
- Return `container_cleanup: confirmed` with timeout evidence when cleanup succeeds.

## Security and lifecycle properties

- Container names are generated internally; task content cannot choose a name or command.
- The cleanup command uses an argument array, never a shell.
- Cleanup has its own bounded 10-second deadline.
- This change does not enable the worker, change queue admission, grant network access, or authorize patch approval, merge, deployment, or release.
- The runner remains opt-in and must still be wired through the governed PostgreSQL task/workspace lifecycle before production activation.

## Tests added

- A simulated Docker CLI timeout verifies that the named daemon container receives a force-removal request and the result records confirmed cleanup.
- A cleanup failure verifies fail-closed behavior.

## Remaining Phase 17 work

- Connect the container runner to the approved-plan worker lifecycle and persist execution/test evidence.
- Add process/container cancellation tied to lease loss and worker shutdown, not only test-command timeout.
- Validate Docker daemon isolation, read-only host mounts where feasible, workspace ownership, rootless/runtime policy, and deployment-specific security controls.
- Add explicit opt-in worker bootstrap, uncertain-job reconciliation, staging contention tests, and operational metrics/alerts.
