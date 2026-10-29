# Kanyoza P29 — Explicit Council Approval Fence

**Date:** 2026-10-22  
**Scope:** Condition Engine Council authorization boundary and regression validation.

## Executive result

P29 closes a consequential fail-open gap in the Condition Engine's governed execution adapter. Previously, the adapter returned early for blocked or escalated Council decisions, but did not require a positive approval signal before proceeding. An unknown, missing, or unrecognized Council decision could therefore fall through toward execution.

The adapter now requires `decision.is_approved` to be explicitly truthy. If approval is not confirmed, it returns `ok: false`, `success: false`, `governance_unverified: true`, and `council_approval_not_confirmed`. This check occurs before constructing the execution ledger/executor or invoking the content mutation.

## Code changes

1. `council/integration/condition_engine_integration.py`
   - Added an explicit Council approval gate after blocked/escalated handling.
   - Missing/unknown approval fails closed.
   - Escalation results now consistently include `ok: false` and Council decision evidence.
2. `tests/test_condition_engine_council_approval_fence.py`
   - Verifies unknown Council state does not instantiate the execution boundary.
   - Verifies explicit approval reaches the governed execution boundary using a fake executor and fake receipt.
   - No external mutation or live provider is called.
3. `scripts/audit_meta_condition_engines.py`
   - Adds a source-contract check for the explicit Council approval fence.
   - Updates the audit conclusion to retain live-runtime and durability limitations.

## Validation

- Focused governance/readiness/execution regression tests: **81 passed, 0 failed**.
- In-process Meta/Condition audit: **11/11 checks passed**.
- Python compilation for changed modules, lifecycle, audit script, and tests: **passed**.
- Archive integrity: verified after packaging.

## Safety and limits

The approved-path integration test uses fakes. It verifies control-flow and receipt propagation shape, not a live Council deliberation, database-backed receipt, real content mutation, or authoritative postcondition. No external services were contacted and no real action executor was invoked.

Still required in isolated staging:
- Confirm live lifecycle binds the real Council-governed executor.
- Verify an approved action creates a durable ledger record and authoritative receipt.
- Verify rejected, escalated, and unknown Council decisions never cause side effects.
- Verify postconditions from the authoritative system and reconcile uncertain outcomes.
- Run the full suite in an environment with its required optional dependencies and backing services.
