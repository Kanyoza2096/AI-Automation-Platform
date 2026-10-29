# Phase 5 — Intelligence ActionIntent → Canonical Action Kernel Handoff

## Purpose

This slice closes the Phase 5 execution-boundary seam without creating a second execution engine.

Canonical path:

`Signal → Interpretation → Decision → Recommendation → Intelligence Policy Governance → ActionIntent Admission → Canonical Authorization/Decision → Action Kernel`

The intelligence layer remains unable to authorize or execute external work by itself.

## Boundary Contract

1. A persisted/rehydrated Intelligence ActionIntent must have a valid `ActionIntentAdmission`.
2. Admission is revalidated against the authoritative Evidence Graph, policy, and governance evaluation at handoff time.
3. Tenant/workspace identity must match the authenticated `RequestContext`.
4. The canonical KDO request pipeline performs authorization and creates an action-scoped DecisionGate decision with `enqueue=False`.
5. A blocked canonical decision stops the handoff before the Action Kernel.
6. Only an allowed canonical decision is supplied as `decision_id` to the existing Action Kernel.
7. The Action Kernel remains the only durable action admission/execution boundary.
8. The handoff preserves the intelligence ActionIntent idempotency key.
9. Intelligence provenance is durably carried in the Action Kernel parameters under `_kdo_intelligence`.
10. No provider, connector, adapter, or external side effect is invoked by the handoff layer.

## Safety Semantics

- `REJECTED` intelligence governance: no ActionIntent admission, no handoff.
- `HOLD_UNCERTAIN`: no ActionIntent admission, no handoff.
- `REQUIRES_APPROVAL`: no ActionIntent admission, no handoff until the canonical approval path exists.
- `APPROVED`: may cross the intelligence admission boundary, but still requires canonical KDO authorization before entering the Action Kernel.
- Canonical authorization rejection: Action Kernel is not called.
- Workspace mismatch: rejected before canonical authorization and kernel enqueue.
- Admission fingerprint mismatch: rejected before canonical authorization and kernel enqueue.
- Duplicate delivery: the original idempotency key is preserved; the Action Kernel remains responsible for the durable duplicate fence.

## Files

- `services/intelligence_action_kernel_handoff.py`
- `tests/architecture/test_intelligence_action_kernel_handoff.py`
- Phase 5 intelligence evidence/governance/admission services copied into the patched project

## Verification

Focused handoff suite: **8 passed**.

Full architecture suite: **364 passed**.

Python compilation: **PASS**.

The real Action Kernel was exercised in-memory to prove the handoff reaches the canonical durable-intent boundary without executing a provider.

## Authority Model

- Intelligence Policy Governance: governance evidence only.
- Intelligence ActionIntent Admission: admission evidence only.
- Canonical KDO Authorization/Decision Pipeline: authorization/decision authority.
- Action Kernel: durable action admission and execution authority.
- Connector Fabric / provider adapters: provider execution authority beneath the kernel.

No intelligence-side provider execution path was introduced.
