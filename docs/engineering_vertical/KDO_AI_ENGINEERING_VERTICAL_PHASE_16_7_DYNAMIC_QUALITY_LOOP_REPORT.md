# KDO AI Engineering Vertical — Phase 16.7 Dynamic Quality Loop

## Objective

Extend the existing KDO engineering vertical so generated work can pass through a bounded test → diagnose → repair → retest → independent verification loop, using the existing `AgentRegistry`, existing role capability metadata, the existing bounded command runner, and the existing Git patch primitive. This phase does not introduce a new permanent agent roster.

## Implementation

- Added `services/kdo_engineering_phase16_7.py`.
- Added `run_dynamic_quality_loop(...)`, which selects installed role adapters by capability from the existing registry and orchestrates test evidence, diagnosis, patch proposal, host-authorized patch application, retesting, and independent verification.
- Added `make_workspace_test_runner(...)` to adapt the existing bounded/allowlisted `services.kdo_engineering_runner.run_command` runner.
- Added `make_workspace_patch_applier(...)` to adapt the existing checked `git apply --check` / `git apply` primitive.
- Added `make_dynamic_quality_executor(...)` as an explicit wrapper around the existing Phase 16.2 build executor. It allows the production composition root to inject task-aware registry, test-command, repair-capability, and authorization factories.
- Updated `KDOPhase162Workflow.execute_approved_build(...)` so an executor result containing a `quality_loop` status other than `awaiting_authorization` transitions the task to `BLOCKED` and marks the workspace `quality_blocked`, preserving evidence and preventing it from being treated as review-ready.
- Added tests for the loop, dynamic registry selection, authorization denial, repair exhaustion, independent review, runner adapter, executor wrapper, and blocked workflow transition.

## Safety properties

- Agent outputs are proposals; they do not independently authorize execution or release.
- Patch application and test execution each require a positive authorization callback result.
- A passing test requires a host-runner result with `status == "passed"` and `exit_code == 0`.
- Repair attempts are bounded to 0–5.
- The verifier's runtime identity must differ from the repair agent's identity.
- Successful verification yields `awaiting_authorization`, never release authorization.
- Missing capabilities, malformed output, denied authorization, failed tests, or exhausted repairs fail closed.
- Workspaces remain the host's responsibility to isolate. The local command runner is not an OS/container sandbox.

## Explicit integration contract

The existing production entrypoint must explicitly pass the result of `make_dynamic_quality_executor(...)` as the `executor` callback to `KDOPhase162Workflow.execute_approved_build(...)`. It must provide the real Action Kernel-backed authorization function, a task-aware runtime adapter/registry factory, test command policy, and repair-capability selection. This phase does not silently discover providers, create live agents without a host adapter, or replace the authoritative Action Kernel.

The PostgreSQL workflow composition factory does not automatically configure these runtime dependencies. Production integration remains opt-in and must be validated in staging.

## Validation

See `KDO_AI_ENGINEERING_VERTICAL_PHASE_16_7_VALIDATION_REPORT.md` for executed test results and limitations.
