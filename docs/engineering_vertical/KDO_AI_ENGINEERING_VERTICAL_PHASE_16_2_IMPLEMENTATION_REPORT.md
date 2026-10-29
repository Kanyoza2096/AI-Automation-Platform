# KDO AI Engineering Vertical — Phase 16.2 Implementation Report

## Scope delivered

Phase 16.2 adds a persisted, scope-authorized orchestration path that connects the Phase 16.1 Git worktree lifecycle to the SQLite engineering task repository. Build execution is not reachable until a plan is submitted, independently reviewed, and explicitly approved. The build callback receives the managed task worktree path rather than the source repository path.

### Lifecycle and policy

1. `create_task` checks actor authorization, only accepts unreviewed `PROPOSED` tasks, rejects caller-supplied approval/build evidence, and relies on the task repository's tenant/workspace/idempotency constraints.
2. `submit_plan` stores ordered plan steps, submitter identity, and a SHA-256 digest in task evidence using revision compare-and-swap.
3. `review_plan` requires an authorized reviewer distinct from the submitter. Approval transitions `PROPOSED -> PLANNED`; rejection transitions `PROPOSED -> BLOCKED`.
4. `execute_approved_build` checks actor authorization, task scope/status, approval state, and plan digest before transitioning `PLANNED -> QUEUED -> RUNNING` and creating an isolated Git worktree.
5. Workspace metadata (tenant, workspace, task, path, branch, base/head revision, status) and a separate durable plan-review record (decision, reviewer, digest) are persisted in `SQLiteEngineeringWorkspaceRepository`, with scope-keyed reads, unique paths, and a unique `(tenant_id, workspace_id, task_id)` identity. Execution requires the separate approval record, so caller-supplied task evidence alone cannot authorize a build.
6. A successful callback moves the task to `AWAITING_REVIEW`, not `COMPLETED`. A callback exception blocks the task, records that automatic resume is disabled, and preserves the worktree for investigation.
7. `reconcile_workspaces` inspects recorded worktrees in the requested tenant/workspace scope. Missing worktrees and branch/revision/dirty-state drift are recorded; reconciliation never deletes a worktree or resumes a task.

## Changed files

- `services/kdo_engineering_phase16_2.py` — new approval-gated isolated-worktree orchestration and durable workspace metadata repository.
- `services/kdo_engineering_task_repository.py` — added `update_evidence` with transaction, revision CAS, fingerprint validation, and scope enforcement.
- `services/kdo_engineering_persisted_workflow.py` — exports Phase 16.2 orchestration classes as the safe workflow entry point for callers migrating from the legacy patch runner.
- `tests/engineering_vertical/test_phase16_2_workflow.py` — eleven regression tests for authorization denial, rejection, duplicate/idempotent task creation, isolated worktree use, duplicate execution, interruption preservation, tenant isolation, independent approval, plan immutability, metadata persistence, dirty-worktree reconciliation, and confirmed-dead-worker recovery.
- `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_2_IMPLEMENTATION_REPORT.md` — implementation and limitations report.
- `docs/engineering_vertical/KDO_AI_ENGINEERING_VERTICAL_PHASE_16_2_VALIDATION_REPORT.md` — exact validation commands/results.

## Security and release boundary

- Every public orchestration action checks a supplied authorization callback with actor, action, tenant, and workspace scope. The production callback must resolve authenticated identities and authoritative permissions; a caller-provided permissive callback is not an identity system.
- Task reads, transitions, and workspace metadata lookups are tenant/workspace scoped. SQLite uniqueness and compare-and-swap protect local persistence; they do not provide cross-process distributed leases.
- The task contract and this workflow do not grant release authority. Builds stop at `AWAITING_REVIEW`; no merge, push, deployment, or release authorization was implemented or performed.
- The callback is an integration seam, not a sandbox. It must not be used to run untrusted generated code on a shared host.

## Remaining requirements before staging/production

- Replace local SQLite task/workspace stores with the authoritative production database and enforce tenant/workspace constraints, transactions, migrations, and compare-and-swap across processes.
- Add durable worker queue/leases, heartbeats, fencing tokens, retry budgets, and crash recovery. Reconciliation blocks a running task only when an injected worker-liveness probe explicitly confirms the worker is absent; unknown liveness leaves it untouched. It never automatically resumes work.
- Execute agents and tests in an actual OS/container/VM sandbox with CPU, memory, network, filesystem, credential, and process limits; implement secrets isolation and egress policy.
- Integrate the authorization callback with authenticated API principals, workspace membership, audit logging, and the authoritative policy/Action Kernel. Add API routes/UI only after their auth model is reviewed.
- Add provider/agent integration, bounded test runner policy, artifact collection, reviewer workflow, and immutable execution receipts. A successful callback currently records its returned mapping but does not itself prove tests passed.
- Test concurrent workers, process kills at every lifecycle boundary, filesystem races/symlink attacks, database migration/backup/restore, retention, monitoring/alerts, load, and failure injection.
- Run real staging acceptance with credentials and infrastructure. Docker/staging deployment, external integrations, production readiness, and release approval remain unverified and intentionally out of scope.
