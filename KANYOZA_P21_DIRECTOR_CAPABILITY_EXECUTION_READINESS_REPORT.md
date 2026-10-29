# Kanyoza P21 — Director Capability Execution-Readiness Audit

## Objective

Improve the Director's ability to distinguish a registered capability from a capability whose handler, authorization requirements, governed execution path, and outcome-verification contract are actually ready.

## Implemented

### 1. Expanded capability readiness catalog

`Director.capability_catalog()` now reports, per capability:
- declared and policy-effective authority;
- whether Director policy requires approval;
- API authentication/scope expectations for catalog reads and direct execution;
- handler binding and handler name;
- whether the handler implements an operation rather than intentionally blocking it;
- execution route (`director_task_handler`, `proposal_governed_handoff`, or `safety_blocked`);
- expected execution boundary;
- governed handoff binding;
- outcome predicate and verification window;
- actionable issue codes and a conservative `ready` value.

A callable `_blocked` handler is no longer mistaken for a functioning implementation. A capability with a placeholder handler, missing proposal handoff, missing configured outcome predicate, or disabled status remains not ready.

### 2. Direct execution no longer bypasses Director governance

`Director.execute_capability()` now refuses to execute proposal-capable operations through the legacy direct path. These must use the proposal-backed governed handoff and canonical receipt contract. Capabilities for which policy requires approval return `approval_required` rather than invoking the handler. The existing authority fence, when configured, remains checked first. Enabled, bound AUTO capabilities can still use this direct path.

### 3. API readiness summary

`GET /capabilities` now adds counts for approval-required capabilities, missing outcome verification, intentionally safety-blocked capabilities, and distribution by execution path. The existing catalog response remains available and adds fields rather than removing the existing ones.

## Validation

- Focused Director and related regression suite: **146 passed**.
- Python compilation: passed (`python -m compileall -q services/director tests/test_director_capability_catalog.py`).
- ZIP integrity: checked after packaging.

Test scope included Director capability catalog, auth boundary, execution idempotency, engine signals, engine health truth, backend route contracts, and P16–P20 signal/intelligence regression tests.

## Important interpretation

`ready` is a wiring and contract diagnostic, not an authorization grant. Request authentication, platform scope, Director policy, task/proposal state, the authority fence, and the canonical action boundary continue to control whether execution is permitted. This local audit does not prove that external services, credentials, Supabase, or production Action Kernel dependencies are live and healthy.

## Files changed

- `services/director/core.py`
- `services/director/api.py`
- `tests/test_director_capability_catalog.py`

## Next recommended phase

P22 should connect readiness findings to the live service/container binding report and validate the full registered → bound → authorized → executed → outcome-verified path using controlled integration tests, while preserving no-side-effect defaults for production-sensitive actions.
