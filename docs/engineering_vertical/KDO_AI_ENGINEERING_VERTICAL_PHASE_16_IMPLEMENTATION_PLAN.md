# KDO AI Engineering Vertical — Phase 16 Implementation Plan

**Status:** Design/implementation plan only. This document does not claim Phase 16 code has been implemented or production-validated.

## 1. Objective

Evolve the Phase 15 dependency-aware coordinator into a native engineering workspace where a user can inspect and approve a plan, agents work in isolated Git workspaces, long-running jobs survive process interruption, every patch is reviewable, and release remains behind an independent authorization gate.

## 2. Baseline findings from the Phase 15 package

The package already contains useful building blocks:

- `services/kdo_engineering_scheduler.py`: in-process dependency graph scheduling with cycle validation and bounded worker count. Its own module documentation says it is not a durable worker queue.
- `services/kdo_engineering_repository.py`: bounded Git inspection and patch application primitives. Its documentation explicitly says this is not a sandbox and requires a disposable isolated workspace.
- `services/kdo_engineering_orchestrator.py`: single-task patch/test flow with an injected authorization callback and evidence capture. It does not commit, push, or deploy.
- `services/kdo_engineering_persisted_workflow.py`: persisted task lifecycle transitions and conservative recovery of orphaned running tasks; it does not automatically resume ambiguous work.
- `services/kdo_engineering_task_repository.py`, `services/kdo_engineering_artifacts.py`, `services/kdo_engineering_release_gate.py`, `services/kdo_engineering_tool_policy.py`, `services/kdo_engineering_receipts.py`, and `services/kdo_engineering_staging.py`: existing persistence, artifact, policy, receipt, and staging components to reuse after contract review.
- `tests/engineering_vertical/`: focused unit/contract tests for repository, scheduler, task lifecycle, artifacts, release gate, tool policy, and staging lifecycle.

The Phase 15 completion report records **121 passed** for its stated focused regression command. The separate user-provided milestone summary says **132 passed**. Treat these as different/unreconciled test runs until a reproducible command and test inventory explain the difference. ZIP integrity and compilation are not equivalent to live staging acceptance.

## 3. Non-negotiable architecture rules

1. Planning is read-only. Planning agents cannot write files, run arbitrary shell commands, deploy, or grant themselves permissions.
2. Plan approval is a persisted, revision-bound decision. Any material plan change invalidates the approval and requires re-approval.
3. Every build task uses a dedicated isolated workspace/worktree with explicit tenant, project, task, base commit, resource limits, and cleanup policy.
4. Do not run untrusted generated code in the API process. Execute builds/tests in disposable containers or equivalent isolated workers with CPU, memory, disk, process, and wall-clock limits; network is deny-by-default and allowlisted only by policy.
5. Durable task state and worker ownership are authoritative. In-memory futures are execution details, not the source of truth.
6. Worker claims use leases, fencing tokens, compare-and-swap revisions, and idempotency keys. An expired lease alone does not prove that a side effect did not occur.
7. A retry/recovery decision checks the task's last durable event, workspace state, side-effect class, and idempotency support before replay.
8. Patches are proposals until machine verification and independent review succeed. Agent-authored claims are not machine-observed evidence.
9. Evidence is immutable or append-only, content-addressed, scoped to tenant/project/run, and records provenance, timestamps, tool versions, commit IDs, exit codes, and truncation.
10. Release authorization remains separate from build/review. No successful test, agent decision, or report can implicitly release or deploy.
11. Secrets never enter prompts, task logs, diffs, artifact metadata, or diagnostic output. Tenant isolation is enforced at every read and write boundary.
12. Fail closed when the authoritative authorization adapter, durable store, worker lease service, or required evidence is unavailable.

## 4. Target lifecycle

`draft_plan → awaiting_plan_approval → approved → queued → claimed → running → verifying → awaiting_independent_review → awaiting_release_authorization → released`

Terminal or exceptional states include `rejected`, `blocked`, `failed`, `cancelled`, and `expired`. The implementation must define allowed transitions centrally and reject invalid transitions. A plan revision and task revision must be included in every authorization decision and evidence record. `released` is only reachable through the authoritative release gate.

## 5. Work packages

### 16.1 — Contracts and plan approval

**Reuse:** `services/kdo_engineering_vertical_contracts.py`, `services/kdo_engineering_task_repository.py`, `services/kdo_engineering_gateway_workflow.py`, `services/kdo_engineering_prompt_assembler.py`.

**Implement:** versioned `EngineeringPlan` and `PlanApproval` contracts; canonical plan fingerprint; validated task DAG; task-to-plan revision binding; approval/rejection endpoints or service methods; expiration and re-approval on plan mutation; audit events for every transition.

**Acceptance:** invalid graphs never dispatch agents; a changed plan cannot reuse stale approval; approval is tenant/project-scoped and actor-attributed; planning is provably read-only.

### 16.2 — Isolated workspace and checkpoint manager

**Reuse cautiously:** `services/kdo_engineering_repository.py` and existing artifact APIs. Do not treat current repository helpers as a sandbox.

**Implement:** `services/kdo_engineering_workspace_manager.py` (proposed), with a provider interface for creating/deleting disposable Git worktrees or cloned workspaces; immutable base commit; per-task branch/worktree; baseline status check; patch application; checkpoint creation; diff/stat capture; conflict detection; cleanup/quarantine; retention policy.

**Rules:** never apply agent patches to the user's primary checkout; reject dirty or unexpected baseline state unless an explicit policy handles it; use safe path handling and no shell interpolation; enforce storage quotas; preserve a workspace for investigation when cleanup is unsafe.

**Acceptance:** concurrent tasks cannot see or mutate one another's files; base commit and patch digest are reproducible; failed cleanup quarantines the workspace and blocks reuse; no force reset or blanket clean can destroy unrelated work.

### 16.3 — Durable worker execution and leases

**Reuse:** `services/kdo_engineering_scheduler.py` for dependency semantics only; `services/kdo_engineering_persisted_workflow.py` and task repository for lifecycle patterns.

**Implement:** durable queue/outbox integration using the project's approved persistent infrastructure; worker claim/heartbeat/lease expiry; monotonically increasing fencing token; bounded concurrency by tenant/project/provider; cancellation; heartbeat and progress events; dead-letter/blocked state; startup reconciliation.

**Recovery:** do not automatically replay an ambiguous running task. Reconcile worker lease, execution ID, workspace status, process/container status, and last persisted event. Resume only from a verified checkpoint when the operation is idempotent and the fencing token is current; otherwise mark blocked for safe operator review.

**Acceptance:** worker crash before claim, after claim, during patch, during tests, and after tests-before-result-persist are tested; duplicate deliveries do not duplicate accepted results or external side effects; lease loss prevents stale workers from committing state.

### 16.4 — Sandboxed tool runner

**Reuse:** `services/kdo_engineering_runner.py` and `services/kdo_engineering_tool_policy.py` only after security review.

**Implement:** a worker adapter to disposable containers/VMs; non-root identity; read-only base image; bounded writable workspace; no host socket or host credential mounts; syscall/capability restrictions; explicit environment allowlist; output redaction/truncation; hard timeout and process-tree termination; optional dependency cache that cannot cross tenant boundaries.

**Network:** default deny. Any package registry or external endpoint access requires a scoped allowlist, request logging, and egress enforcement outside the agent process.

**Acceptance:** test escape attempts, path traversal, environment/secret exfiltration, fork/resource exhaustion, timeouts, malicious test scripts, and network denial. Passing unit tests alone is not proof of sandbox security.

### 16.5 — Verification and independent patch review

**Reuse:** `services/kdo_engineering_artifacts.py`, `services/kdo_engineering_receipts.py`, release-gate components, and existing review-related contracts after inspection.

**Implement:** verification manifest recording exact commit/base, patch SHA-256, test command identity, environment/image digest, tool versions, exit code, stdout/stderr artifact digests, static checks, and coverage or other configured gates. Independent reviewer receives the diff and machine evidence, not only the implementer's summary. Record findings and disposition; re-review any patch changed after review.

**Acceptance:** missing, stale, mismatched, or truncated-critical evidence cannot satisfy a required gate; a patch digest mismatch invalidates review; reviewer identity/role is distinct from implementer for required independent review.

### 16.6 — User-facing engineering task board

**Frontend status:** no frontend parity should be assumed from the backend package. Locate the actual frontend application and its established design system before adding screens; do not create a second disconnected UI.

**Views:** project/repository selector; Plan mode; dependency graph and task list; approval diff; active run board; per-task logs/progress; workspace/branch and checkpoint status; unified patch viewer; verification results; review comments; authorization history; audit/evidence timeline.

**UX requirements:** show state provenance and freshness; distinguish model-reported from machine-observed evidence; accessible keyboard navigation; responsive mobile layout; clear blocked/retry/cancel actions; destructive or externally visible actions require confirmation and authorization. No fake green statuses or mock success fallbacks.

**Acceptance:** UI state comes from persisted API state; refresh/reconnect restores current job state; users cannot bypass backend authorization by calling UI routes directly; tenant/project scope is tested server-side.

### 16.7 — Staging acceptance and release boundary

Use the existing staging guide and gate as the starting checklist. Deploy only to a specifically authorized staging environment. Run cross-tenant negative tests, lease-loss tests, process-kill recovery, workspace isolation tests, patch tampering tests, stale-plan approval tests, review independence tests, and release-denial tests. Exercise at least one complete low-risk repository change from plan to reviewed patch to awaiting release authorization.

**Required evidence:** deployment/image digest; configuration validation without secret disclosure; migration result; test command and raw artifacts; workspace isolation report; authorization decision/receipt; worker recovery report; cross-tenant denial results; reviewer decision; explicit confirmation that release was not performed without approval.

Production remains blocked until the designated owner reviews all staging evidence and signs the release decision.

## 6. Suggested file-level change map

| Area | Proposed action | Reason |
|---|---|---|
| `services/kdo_engineering_vertical_contracts.py` | Extend carefully with plan revision, approval and lifecycle contracts | Keep state transitions typed and centrally validated |
| `services/kdo_engineering_task_repository.py` | Extend through migrations/CAS only after schema review | Persist approval revision and worker fencing/lease metadata |
| `services/kdo_engineering_scheduler.py` | Preserve DAG scheduler; add an adapter, not durability claims | In-process scheduling remains useful but is not a durable queue |
| `services/kdo_engineering_repository.py` | Keep low-level Git operations; call only inside isolated workspaces | Git safety is not process/container isolation |
| `services/kdo_engineering_orchestrator.py` | Refactor to consume a workspace handle and execution receipt | Prevent direct execution against arbitrary project checkout |
| `services/kdo_engineering_persisted_workflow.py` | Add lease-aware reconciliation and explicit resumability policy | Current conservative blocking behavior is a safe foundation |
| `services/kdo_engineering_artifacts.py` | Add immutable verification manifests/provenance where needed | Review must bind to exact patch and environment |
| `services/kdo_engineering_release_gate.py` | Preserve and test as independent authority boundary | Build success must never imply release permission |
| `services/kdo_engineering_workspace_manager.py` | Add | Workspace lifecycle, checkpoints, quarantine and cleanup |
| `services/kdo_engineering_worker_leases.py` | Add if not already provided by approved queue infrastructure | Claim, heartbeat, fencing and expiry semantics |
| `services/kdo_engineering_sandbox.py` | Add worker-side adapter to actual isolation provider | Separate untrusted execution from API process |
| `api/` engineering routes | Extend existing route registration after discovering current API map | Avoid duplicate or unregistered endpoints |
| Existing frontend | Extend established application, routes, components and auth | Avoid disconnected dashboard or fake data |
| `tests/engineering_vertical/` | Add contract, security, crash/recovery, concurrency and API tests | Test invariants, not only happy paths |
| `docs/engineering_vertical/` | Keep this plan and publish actual acceptance evidence per run | Clear difference between design and verified delivery |

This is a proposed change map. Before coding, trace current imports, route registration, dependency injection, database migrations, tenant scoping, and test fixtures to avoid duplicating already-present services.

## 7. Verification matrix

- DAG: unknown dependency, duplicate ID, cycle, independent parallel tasks, failed prerequisite propagation.
- Approval: missing approval, stale plan hash, changed acceptance criteria, wrong tenant/project, revoked actor authority.
- Workspace: dirty base, concurrent worktrees, patch conflict, malicious path, cleanup failure, immutable base commit.
- Worker: duplicate delivery, lease expiry, stale fencing token, crash at each lifecycle boundary, cancellation race.
- Sandbox: CPU/memory/disk/process exhaustion, timeout, denied network, secret leakage, host filesystem/socket access.
- Evidence: patch digest mismatch, stale test output, wrong base revision, output truncation, missing artifact, cross-tenant retrieval attempt.
- Review/release: implementer cannot satisfy independent-review gate; post-review patch change invalidates approval; release fails closed without authoritative authorization.
- UI/API: refresh/reconnect, pagination, role restrictions, tenant isolation, concurrent updates, no mock-success fallback.
- Staging: deploy, run migrations, execute full acceptance suite, archive artifacts, review blockers; do not infer production readiness from local tests.

## 8. Definition of done

Phase 16 is complete only when all required work packages are implemented and reviewed, migrations are reversible or have a documented recovery strategy, unit and integration tests pass from a recorded clean checkout, worker crash scenarios are demonstrated, the frontend is wired to real persisted APIs, sandbox controls are externally enforced, and staging evidence is reviewed. Release authorization remains disabled until a separate explicit decision. A design document, passing unit suite, or ZIP integrity check alone is not Phase 16 completion.

## 9. Recommended implementation sequence

1. Inventory current route registration, task schema/migrations, authorization adapter, artifact storage, queue infrastructure, and frontend entry points.
2. Define state transition table, plan approval contract, and invariant tests before implementing UI.
3. Build isolated workspace manager and prove parallel-task isolation.
4. Integrate durable worker leases and conservative crash reconciliation.
5. Integrate sandbox execution and security tests.
6. Bind verification manifests and independent review to immutable patch digests.
7. Build task board against persisted APIs.
8. Execute the staging acceptance matrix and publish measured results.

No implementation timeframe is prescribed; sequencing is based on dependency and risk, not calendar estimates.
