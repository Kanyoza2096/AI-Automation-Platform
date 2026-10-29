# KDO Engineering Vertical — Phase 16.12 Container Worker Adapter

## Implemented

- Added a Docker-backed workspace test runner with `--network none`, read-only container root, dropped Linux capabilities, `no-new-privileges`, non-root UID, bounded PID/memory/CPU, bounded timeout/output, and a single task-workspace bind mount.
- Test profiles are fixed allowlisted command arrays (`pytest`, `unittest`, `compileall`); no shell or user-selected executable is used. The worker image is operator-configured and should be pinned by digest.
- Added a quality-loop adapter returning explicit machine-observed exit status, timeout, command, and sandbox metadata.
- Added unit tests for isolation flags, workspace boundary checks, allowlist enforcement, and result normalization.

## Lifecycle status

This adapter is not yet exposed as active sandbox execution in the console provider. The durable PostgreSQL approval/review lifecycle must be connected to per-task workspace provisioning, worker leasing/fencing, patch application, test/retest evidence persistence, cancellation, and cleanup before the capability flag can be enabled. It is intentionally fail-closed until that wiring is complete.
