# Phase 5.5 — Intelligence Decision Engine / Learning Consumption

## Boundary

`Learning Feedback -> Bounded Learning Decision Context -> Decision Construction -> Recommendation -> Governance`

The Decision Engine is the canonical construction point for Phase 5 intelligence decisions. It consumes the bounded learning context as advisory evidence and records the exact learning-use policy and source fingerprints in decision/recommendation provenance.

## Default semantics

`REFERENCE_ONLY` is the default. Learning does not change action type, risk class, confidence, uncertainty, evidence references, or autonomy level. Therefore the canonical Governance/Authorization behavior remains unchanged for otherwise identical decision inputs.

`IGNORE` is available when a decision policy explicitly excludes learning context from the decision provenance.

Learning context cannot provide policy/action/approval/autonomy overrides, grant authority, claim causation, mutate historical execution, invoke providers, or invoke the Action Kernel.

## Verification

- Focused Decision Engine tests: 5 passed
- Full architecture suite: verified in release
- Workspace mismatch: fail closed
- Tampered learning context: fail closed
- Same decision inputs with/without learning: governance semantics preserved
- Learning provenance: auditable and fingerprinted
