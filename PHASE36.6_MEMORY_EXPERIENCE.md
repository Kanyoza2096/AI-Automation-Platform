# Phase 36.6 — Memory + Experience Foundation

Kanyoza now has a bounded, tenant-scoped organism memory contract with three explicit memory classes: episodic, semantic, and procedural. Experiences capture an attempted action, goal linkage, outcome, result, lesson, and confidence, and can be recorded as episodic memory.

## Authority boundary

Memory is evidence, not authority. It cannot grant permissions, bypass Guardian/DecisionGate, or become authoritative for external execution. Durable execution remains governed by the execution ledger and provider receipts.

## Design

- Workspace isolation on every memory and experience.
- Provenance and confidence are mandatory.
- Timestamps must be timezone-aware.
- Memory retention is bounded per workspace.
- Queries are bounded and optionally filtered by kind/tags.
- Experiences explicitly preserve uncertain outcomes rather than treating ambiguity as success.
- Semantic and procedural memories are contracts for future learning; this phase does not invent an unsafe self-modification loop.

## Verification

- Full test suite: 160 passed.
- Production artifact gate: PASS.
- Existing `plugins.council` deprecation warning remains unchanged.
- Release version: 14.7.4.
- SQL migrations: 39.

## Next integration

The next useful step is to connect experience recording to existing execution receipts and outcome feedback, then add retrieval into planning without allowing memory to override current authoritative state.
