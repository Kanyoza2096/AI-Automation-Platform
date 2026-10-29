# KDO Phase 16.1 — Isolated Git Workspace Lifecycle

## Status

Repository implementation slice added; local tests must pass in the target environment before adoption. This is not a production sandbox, durable workspace registry, or release authorization feature.

## Delivered in this slice

- Creates one Git worktree and task-specific branch for each tenant/workspace/task identity.
- Resolves the requested base reference to a commit before creating the worktree.
- Requires the managed worktree root to be outside the source repository.
- Uses fixed Git executable invocation with `shell=False`, bounded timeouts, and a minimized environment.
- Rejects duplicate task workspace paths and invalid identities.
- Provides read-only branch/revision/status/diff inspection.
- Removes only clean managed worktrees by default; dirty worktrees are preserved rather than force-deleted.
- Adds regression tests for isolation, duplicates, unsafe roots, inspection, clean removal, dirty preservation, and invalid inputs.

## Explicit non-goals / remaining requirements

- No API route or UI integration is enabled in this slice.
- No durable database registry or crash-recovery reconciliation is included.
- No OS/container sandbox is provided by Git worktrees; workers still need a hardened isolated runtime with network/secret restrictions and resource limits.
- No automatic merge, push, deployment, or release authorization is performed.
- Branch cleanup and metadata reconciliation across process crashes require the next integration step.

## Integration contract

Before invoking this service, the caller must authenticate the principal, authorize the tenant/workspace/task scope, verify plan approval, and reserve a unique task execution identity. Persist the returned path, branch, base revision, and lifecycle state in the authoritative task repository. On recovery, reconcile persisted records with `git worktree list --porcelain`; do not delete unknown or dirty worktrees automatically.
