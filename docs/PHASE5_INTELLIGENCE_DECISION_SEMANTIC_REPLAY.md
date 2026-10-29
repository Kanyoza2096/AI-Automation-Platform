# Phase 5.11 — Intelligence Decision Semantic Replay

## Purpose

Historical intelligence decisions must prove that the decision was constructed from the exact captured interpretation, policy identity, learning context, and AI evidence. Replay must not silently substitute newer semantics.

## Contract

`Interpretation + Policy + Learning Context + AI Provenance + Decision Payload + Recommendation Payload -> Decision Semantic Fingerprint`

The semantic fingerprint intentionally excludes ephemeral evidence node IDs and timestamps. It includes:

- policy identity and workspace
- learning-use mode/context/source fingerprints
- AI receipt and interpretation semantic fingerprints
- AI provider/model/correlation/request/status
- interpretation semantic payload/provenance
- decision payload
- recommendation payload

## Recovery guarantees

- Restart preserves the exact decision semantic identity.
- Tampered interpretation evidence is rejected.
- Tampered policy context is rejected.
- Tampered learning context identity is rejected.
- Tampered AI interpretation identity is rejected before higher-level decision verification masks the cause.
- Cross-workspace replay is rejected.
- Replay never authorizes, governs, executes, retries, or calls a provider.

## Verification

Focused AI decision/replay/recovery integration: 28 passed.

Architecture suite: 434 passed.

A full repository run was attempted but did not finish within the execution window; therefore no full-repository green claim is made for this release.
