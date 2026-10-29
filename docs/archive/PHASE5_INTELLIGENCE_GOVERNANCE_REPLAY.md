# Phase 5.12 — Decision → Governance Replay Proof

## Purpose

Historical intelligence decisions must never be re-evaluated against today's governance configuration. Phase 5.12 freezes the exact governance policy and evaluation used for a decision and provides a read-only replay verifier.

## Boundary

`DecisionReplaySnapshot → Historical IntelligencePolicy → GovernanceEvaluation → GovernanceFingerprint`

Replay reconstructs the policy from the persisted historical snapshot and evaluates the persisted recommendation against that frozen policy. A caller may provide a current policy only as an integrity comparison; a different policy is rejected rather than substituted.

## Frozen policy semantics

- policy id and workspace
- confidence thresholds by risk
- human approval requirements by risk
- allowed action types
- blocked action types
- maximum uncertainty
- minimum evidence references
- allowed autonomy levels

## Replay protections

- deterministic policy fingerprint
- deterministic governance evaluation fingerprint
- decision fingerprint linkage
- cross-workspace rejection
- tampered policy rejection
- tampered evaluation rejection
- changed current policy rejection
- historical disposition preserved (`APPROVED`, `REQUIRES_APPROVAL`, `HOLD_UNCERTAIN`, `REJECTED`)
- no authorization or execution authority

## Important distinction

Replay may deterministically re-evaluate against the **captured historical policy snapshot** to prove the recorded disposition. It does not call the live policy source and does not create an ActionIntent.
