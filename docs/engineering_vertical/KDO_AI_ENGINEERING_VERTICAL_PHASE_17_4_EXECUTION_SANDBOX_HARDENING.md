# Phase 17.4 — Execution Sandbox Hardening

## Scope

Harden the existing Docker test executor used by the governed engineering workflow. This is defense in depth for untrusted repository code; it is not a claim that a Docker container is equivalent to a dedicated VM or a hardened multi-tenant sandbox.

## Container policy enforced by the runner

Each test invocation receives a uniquely named disposable container and:

- A digest-pinned, operator-selected image; task content cannot select an image or command profile.
- No network namespace connectivity (`--network none`).
- A read-only container root filesystem, non-root UID/GID 65532, all Linux capabilities dropped, and `no-new-privileges`.
- A private IPC namespace, init process, PID ceiling, CPU quota, memory ceiling with swap disabled, bounded shared memory, and a file-descriptor limit.
- A small `/tmp` tmpfs mounted with `noexec`, `nosuid`, and `nodev`.
- A single task-scoped workspace bind mount. It is writable because test tools need to create caches and artifacts; the managed workspace must therefore be disposable and must not contain host credentials or unrelated tenant data.
- A local Docker log driver with a 10 MiB, one-file retention ceiling.
- A minimized Docker CLI environment; host environment variables, Docker credentials, and application secrets are not passed into the container.
- A bounded host-side stdout/stderr tail collector, so noisy test output does not accumulate without limit in Python memory.
- A hard CLI deadline and explicit daemon-side forced container removal after timeout or cancellation. Cleanup failure remains fail-closed.

## Deployment requirements outside this code

The Docker daemon and host are part of the trusted computing base. Production deployment must additionally:

1. Prefer rootless Docker or configure daemon user-namespace remapping; run the worker as a dedicated unprivileged OS identity.
2. Use a dedicated worker host/VM or equivalent isolation boundary, not the web application host.
3. Keep the Docker socket inaccessible to application code and task containers. A process with unrestricted Docker daemon access can generally escape container-level policy.
4. Apply host-level disk quotas and monitoring to workspace storage, image cache, and Docker data-root.
5. Keep the pinned image minimal, patched, scanned, and built outside task execution. Never mount host sockets, credentials, cloud metadata, or host root paths.
6. Restrict outbound network at the host/firewall layer as well as inside the container; verify the daemon does not grant unexpected capabilities or mounts.
7. Set a bounded worker shutdown deadline and retain audit evidence for timeout, cancellation, and cleanup failures.

## Remaining limitations

- The workspace bind mount is writable by design; it is not a read-only source snapshot. Workspace creation and cleanup must be governed by the worker lifecycle.
- Docker flags cannot compensate for a compromised daemon, kernel, host root account, or privileged worker identity.
- The local log-driver retention ceiling limits daemon log storage; the host-side tail collector independently bounds Python memory.
- The test runner does not grant merge, deployment, release, or approval authority.
- Production worker activation remains disabled pending uncertain-job reconciliation and end-to-end staging gates.

## Validation

The focused Engineering Vertical workflow exercises container command policy, timeout cleanup, cancellation cleanup, and bounded output capture, alongside the worker bootstrap and PostgreSQL workflow suites.
