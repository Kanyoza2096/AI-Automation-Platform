# Phase 5.13 — Governance → ActionIntent Replay Proof

## Purpose

Prove that an intelligence `ActionIntent` admitted for execution is exactly the
ActionIntent produced from the historical governed decision. Historical replay
must not substitute a current policy, recommendation, or ActionIntent.

## Boundary

`Decision → Historical Governance Snapshot → ActionIntent → Admission Proof`

The replay repository is read-only. It cannot authorize, enqueue, execute,
retry, or call providers.

## Stored proof

Each replay record preserves:

- ActionIntent node identity and deterministic fingerprint
- historical governance replay identity and governance fingerprint
- historical policy fingerprint
- historical decision fingerprint
- recommendation node identity
- admission fingerprint
- complete ActionIntent payload/provenance

## Replay checks

Replay fails closed on:

- ActionIntent tampering
- ActionIntent/policy substitution
- ActionIntent/decision substitution
- recommendation lineage substitution
- confidence/uncertainty/action-type mismatch
- governance disposition mismatch
- cross-workspace access
- historical governance replay substitution
- current graph ActionIntent mismatch
- admission fingerprint mismatch

The existing `IntelligenceActionIntentAdmissionGate` is re-used for capture,
so this phase does not create a second admission mechanism.

## Authority boundary

Replay is evidence verification only. It grants no authorization and has no
execution authority.
