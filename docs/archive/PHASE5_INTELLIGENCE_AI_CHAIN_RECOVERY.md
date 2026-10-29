# Phase 5.9 — Intelligence AI Chain Recovery

## Boundary

Phase 5.9 binds the durable AI receipt, KDO interpretation, decision, and recommendation into one recoverable historical chain.

```text
AIReceipt
  -> Interpretation
  -> Decision
  -> Recommendation
```

Each record remains independently durable. `IntelligenceAIChainRecoveryRepository` adds a deterministic chain manifest containing the exact node identities, node fingerprints, AI receipt fingerprint, and decision replay identity.

## Recovery guarantees

- Workspace scope is enforced at every boundary.
- The original successful AI receipt is revalidated during recovery.
- Persisted evidence node fingerprints are verified before recovery succeeds.
- Manifest integrity is verified before recovery succeeds.
- Decision replay is verified against the original AI receipt.
- Required signal -> interpretation -> decision -> recommendation edges are required.
- Provider/model configuration changes cannot replace historical receipt identity.
- Tampered evidence, receipt metadata, provenance, or manifest data fails closed.
- Recovery is read-only and has no governance, authorization, execution, retry, or provider authority.

## Persistence hardening

`IntelligenceEvidenceRepository.load_workspace()` now preserves the original `created_at` value and verifies each persisted node fingerprint. This closes a restart-integrity defect where regenerated timestamps changed fingerprints after reload.

## Architecture proof

Focused Phase 5.9 tests: 13 passed.

The architecture suite must remain green before promotion.
