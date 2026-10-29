# KDO AI Software Engineering Vertical — Phase 16.8 Console Implementation Report

## Purpose
Provide the engineering vertical with a first-party user cockpit for submitting build requests, viewing persisted task conversations and agent activity, and taking approval decisions through backend policy controls.

## Delivered
- `templates/kdo_engineering_console.html`: responsive, dark, mobile-aware engineering console with build-request composer, optional acceptance criteria, task list, event conversation, active-agent panel, approval queue, task state/revision, and connection state.
- `routes/kdo_engineering_console.py`: authenticated UI route and workspace-scoped API contract for capabilities, task listing/submission/detail, incremental event retrieval, and approve/reject decisions.
- `main.py`: mounts the console router.
- `tests/engineering_vertical/test_phase16_8_console.py`: provider contract, fail-closed behavior, workspace scoping, task submission, event feed, and revision-bound approval tests.

## No fabricated activity
The browser renders only records returned by the configured engineering-console provider. It does not synthesize agent messages or infer that an agent is active from a historical `agent_message` event. Active agents must be returned by authoritative task state or explicit agent lifecycle events.

## Provider contract
The host must configure `app.state.kdo_engineering_console_provider` with these methods:
- `capabilities(workspace_id=...)`
- `list_tasks(workspace_id=..., limit=...)`
- `submit_task(workspace_id=..., user_id=..., objective=..., acceptance_criteria=..., idempotency_key=...)`
- `get_task(task_id=..., workspace_id=...)`
- `list_events(task_id=..., workspace_id=..., after_sequence=..., limit=...)`
- `decide(task_id=..., workspace_id=..., user_id=..., decision=..., expected_revision=...)`

Methods may be synchronous or asynchronous. The provider must enforce tenant/workspace ownership, durable task/event reads, idempotency, task revision compare-and-swap, and the canonical Action Kernel / approval policy. The router does not treat UI intent as authority.

## Fail-closed behavior
If the provider is absent or lacks a required method, API requests return HTTP 503. The page remains available to show that the runtime is disconnected, but it displays no sample tasks, simulated agent conversations, or fake active agents. Approval routes require workspace-operator authorization and a positive expected revision; the provider remains responsible for policy and durable decision enforcement.

## Important integration boundary
The current repository does not yet configure this provider in the production composition root. Consequently, the frontend and route contract are implemented, but task creation, real-time conversations, and approval actions will not operate end-to-end until the provider is connected to the canonical task repository, dynamic agent orchestration, durable event store, and Action Kernel. This phase does not claim production runtime integration or release readiness.

## Validation
See `KDO_AI_ENGINEERING_VERTICAL_PHASE_16_8_VALIDATION_REPORT.md` for executed checks and limits.
