# KDO Phase 7.5 — Worker Selection & Capacity Coordination

## Status
IMPLEMENTED — FOCUSED GATE PASSED; ARCHITECTURE GATE PASSED.

## Purpose
Select the most suitable bounded autonomous worker for an operational task using capability, permission, capacity, safety posture, autonomy floor, and optional budget eligibility. Selection is advisory and never grants execution authority.

## Boundary
- Worker selection does not authorize actions.
- Worker selection does not invoke the Action Kernel.
- Worker selection does not call providers.
- Worker identity remains non-authoritative.
- Capability permissions remain separate from selection.
- Workspace/tenant isolation is mandatory.
- Capacity and selection records are fingerprinted and tamper checked.

## Implemented
- Worker capacity registry.
- Availability states: available, busy, draining, unavailable.
- Safety posture: normal, degraded, hold, suspended, emergency.
- Concurrent task limits and load scoring.
- Autonomy-level floor.
- Capability + permission eligibility.
- Optional budget eligibility.
- Deterministic scoring and tie-breaking.
- Advisory selection record and deterministic fingerprint.
- Tamper and cross-workspace rejection.

## Validation
- Phase 7.5 focused tests: **7 passed**.
- Python compileall: **PASS**.
- Architecture suite: **456 passed**.
- Broad repository run: **502 passed before stopping** at an existing publishing integration test because the environment lacks the Python `supabase` dependency. This is not a Phase 7.5 failure.
- Phase 7.2 legacy identity test conflict: one existing test still expects `active -> can_execute=True`; that expectation contradicts the corrected Phase 6 authority invariant. Secure implementation remains `can_execute=False`.

## Authority Invariants
```text
authority = none
can_authorize = false
can_execute = false
can_call_provider = false
can_invoke_action_kernel = false
```

## Architectural Position
```text
Governed Task
    ↓
Worker Selection
    ↓
Eligible Worker
    ↓
Task Assignment
    ↓
Governance / Authorization
    ↓
ActionIntent
    ↓
Action Kernel
```

Selection is an eligibility/capacity decision only. It is not an execution decision.
