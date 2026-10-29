# KDO Phase 7.30 — Coordinated Worker Assignment Lifecycle & Synchronization

## Status
CERTIFIED — architecture/integrity scope.

## Purpose
Phase 7.30 synchronizes the lifecycle of a previously admitted multi-worker assignment set while reusing the canonical single-assignment lifecycle boundary. It provides dependency ordering, partial coordination state, cancellation propagation, terminal protection, and exactly-once logical capacity release.

## Lifecycle model
- Group: ASSIGNED -> ACTIVE/PARTIAL -> RELEASED or CANCELLED/BLOCKED.
- Task: ASSIGNED -> ACTIVE -> RELEASED or CANCELLED.
- A task may start only after every declared dependency is RELEASED.
- Cancelling an active task releases its logical capacity through the existing canonical lifecycle and propagates cancellation to downstream dependent tasks.
- Cancelling an assigned-but-not-active task consumes/releases no runtime capacity.
- Terminal task states cannot be transitioned again.
- Individual lifecycle events remain append-only: ACTIVE, RELEASED, and CANCELLED use distinct durable lifecycle event IDs.

## Authority boundary
This subsystem is coordination state only:
- coordination_only=true
- authorization_granted=false
- execution_requested=false
- provider_called=false
- action_kernel_invoked=false
- business outcome is not asserted here

## Validation
- Dedicated Phase 7.30 tests: 5/5 passed.
- Phase 7.24–7.30 focused/regression chain: 34/34 passed.
- Python compileall: PASS.
- Broader architecture/runtime gate: 30/31 passed.

## Known environment limitation
One pre-existing architecture contract fails in the extracted artifact layout:
`test_docker_ci_builds_canonical_root_dockerfile` expects `.github/workflows/docker-build.yml` at `/mnt/data/kdo_phase7/work730/.github/workflows/docker-build.yml`, while the project is extracted below `/mnt/data/kdo_phase7/work730/project`. This is an extraction/CI-layout issue and is not a Phase 7.30 product failure.

## Certification conclusion
Phase 7.30 is certified for its architecture/integrity scope. It does not constitute production execution, provider, infrastructure, disaster-recovery, or physical-device certification.
