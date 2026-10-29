# Phase 5.10 — AI Interpretation Semantic Replay

## Purpose

Close the historical replay seam between a durable `AIReceipt` and the KDO-owned
`interpretation` evidence node.

The interpretation is a deterministic KDO projection of a successful AI receipt.
Historical replay must prove that the persisted interpretation still represents
exactly that receipt; provider/model configuration changes must never substitute
new AI output for historical evidence.

## Canonical boundary

`AIReceipt → interpretation_semantic_fingerprint → Interpretation → Decision`

The semantic fingerprint covers:

- workspace identity
- `structured_output`
- `raw_output`
- AI status
- receipt fingerprint
- provider/model identity
- correlation identity
- request fingerprint
- explicit AI authority boundary

The signal node ID is deliberately excluded from the semantic material because it
is graph placement, not interpretation meaning.

## Fail-closed rules

- Missing semantic fingerprint: reject replay.
- Receipt fingerprint mismatch: reject replay.
- Provider/model/correlation/request/status mismatch: reject replay.
- Interpretation payload mismatch: reject replay.
- AI authority not equal to `none`: reject replay.
- AI authorization/execution flags not explicitly false: reject replay.
- Cross-workspace replay: reject replay.
- Non-successful historical AI receipt: reject replay.

## Decision replay

Decision replay persists the interpretation semantic fingerprint and verifies it
against the durable AI receipt before returning a historical snapshot.

## Chain recovery

The AI chain manifest now binds the interpretation semantic fingerprint in addition
to signal, interpretation, decision, recommendation, and AI receipt identities.

## Authority boundary

This feature is read-only historical verification. It cannot authorize, execute,
retry, call an external provider, alter governance, or rewrite historical truth.
