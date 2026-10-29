# Phase 5.8 — AI Receipt → Interpretation Durability & Replay

## Contract

Historical intelligence must retain the exact AI receipt that produced an interpretation. Provider/model configuration is mutable; historical evidence is not.

Canonical chain:

`AI Provider → AIReceipt → durable receipt fingerprint → Interpretation provenance → Decision provenance → durable replay snapshot`

## Guarantees

- Every successful AI-backed interpretation carries `ai_receipt_fingerprint` plus provider, model, correlation ID, request fingerprint, and AI status.
- `AIReceiptRepository.get_receipt()` reconstructs the original immutable receipt and verifies its SHA-256 fingerprint before returning it.
- Denormalized receipt columns are checked against the canonical receipt JSON, detecting independent metadata tampering.
- `AIReceiptReplayVerifier` validates interpretation provenance against the exact durable receipt and refuses non-successful receipts.
- Decision replay snapshots retain the AI receipt identity, including provider/model/request identity and status.
- Decision replay can optionally verify the original receipt through `AIReceiptRepository`; later provider/model configuration is irrelevant to historical replay.
- Workspace boundaries are enforced for receipt and decision replay.
- Replay has no governance, authorization, execution, retry, or provider invocation authority.
- Existing decision replay databases are migrated by adding the new AI provenance columns without rewriting historical decisions.

## Failure semantics

A missing receipt, workspace mismatch, fingerprint mismatch, metadata mismatch, provenance mismatch, or changed receipt identity is a replay integrity failure. It must not be converted into success or silently substituted with a newer provider result.

## Test evidence

- Focused AI/replay suite: **20 passed**.
- Full architecture suite: **421 passed**.
- Python compilation: passed for the changed modules.
