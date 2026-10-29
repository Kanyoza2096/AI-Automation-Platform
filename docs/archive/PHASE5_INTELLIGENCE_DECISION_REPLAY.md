# Phase 5.6 — Intelligence Decision Provenance & Replay

## Boundary

`Decision Engine -> Durable Decision Snapshot -> Restart/Reconstruction -> Historical Replay`

A historical decision is replayed from its captured semantic snapshot. Replay uses the recorded `learning_context_id` and `learning_source_fingerprints`; it never re-reads newer learning feedback to rewrite history.

## Deterministic identity

Each decision carries `decision_fingerprint`, derived from:

- decision policy identity and workspace
- learning-use mode
- captured learning context identity and source fingerprints
- semantic interpretation content
- decision payload
- recommendation payload

Ephemeral evidence-node IDs and timestamps are deliberately excluded from the semantic fingerprint.

## Safety boundaries

Replay:

- does not authorize
- does not execute
- does not invoke providers/connectors
- does not invoke the Action Kernel
- does not mutate historical evidence
- rejects a supplied learning context whose identity differs from the captured context

## Verification

- Focused decision/replay tests: **9 passed**
- Full architecture suite: **405 passed**
- Python compilation: **PASS**
- Restart/replay persistence: **PASS**
- New-learning isolation: **PASS**
- Deterministic decision fingerprint: **PASS**
