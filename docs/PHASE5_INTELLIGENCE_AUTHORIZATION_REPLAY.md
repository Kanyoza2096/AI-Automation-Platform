# Phase 5.14 — Canonical Authorization Replay Proof

Phase 5.14 freezes the canonical authorization evaluation that occurred after
an intelligence ActionIntent was admitted.

## Boundary

```text
Historical ActionIntent
        ↓
Canonical RequestContext / RBAC inputs
        ↓
Canonical KDO request pipeline
        ↓
Authorization + Guardian + Metabolism + Council + Director decision
        ↓
Durable authorization replay evidence
```

The replay record is evidence only. It does not grant permission, recreate a
live authorization decision, enqueue an Action Kernel intent, or invoke a
provider.

## Historical guarantee

Historical replay does **not** call `run_action_pipeline` again. Therefore a
later role, permission, guardian state, metabolism state, DecisionGate state,
or other live runtime configuration cannot silently change the historical
authorization result.

The record is bound to the exact ActionIntent fingerprint and workspace.
Tampering with the stored authorization context or pipeline result invalidates
the authorization fingerprint and fails closed.

## Live vs historical authorization

- **Live authorization:** current `RequestContext` → canonical pipeline → current
  decision; required before real execution.
- **Historical replay:** stored authorization evidence → integrity verification;
  no live authorization and no execution authority.

This distinction prevents replay from becoming an authorization bypass.
