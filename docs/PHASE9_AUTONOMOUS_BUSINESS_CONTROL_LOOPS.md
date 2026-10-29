# Phase 9 — Autonomous Business Control Loops

Phase 9 converges KDO's existing autonomous loop, business autonomy envelope,
decision gate, workforce, Action Kernel, verification, reconciliation,
provenance and human-takeover boundaries into a bounded control-loop contract.

## Loop

Observe → Detect → Decide → Propose → Existing Governance → Existing Action Kernel → Verify → Reconcile → Learn

The Phase 9 model is an admission/read model. It does not execute providers,
create ActionIntents, authorize actions, or bypass human/Council governance.

## Autonomy modes

- `observe`: monitor only.
- `suggest`: propose actions for existing governance.
- `governed_autonomy`: eligible to enter the existing governed execution path,
  never direct execution.
- `human_only`: escalate.
- `paused`: block.

## Safety controls

Workspace mismatch, disabled loops, unknown/low-confidence observations,
non-allowlisted actions, cooldowns and repeated failures are explicit signals.
Repeated failures can force human takeover. The loop cannot grant authority.

## Non-duplication

This is not a second agent runtime, workflow engine, Action Kernel,
authorization system, execution ledger, provider registry, or reconciliation
engine. It composes existing KDO primitives.
