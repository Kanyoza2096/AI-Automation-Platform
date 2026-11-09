# U2 durable event provenance integration design

Status: design contract only. This document does not authorize a staging migration, key creation, deployment, or producer enablement.

## 1. Objective

Make producer provenance survive the actual event transport boundaries without turning descriptive metadata into identity proof. A consumer may treat an event as trusted for a governed decision only after verifying a producer-specific signature against the exact persisted event and its tenant scope.

This work covers both event paths:

- `UnifiedEventBus`: in-process dispatch plus its asynchronous write to `public.events`.
- `SharedEventBus`: cross-process publication, database polling, durable delivery receipts, retries, and replay.

Fixing only one path is insufficient. A signed envelope copied into a payload field or a caller-supplied `source_service` / `source_instance` value is not provenance.

## 2. Trust model

Keep these identities distinct:

- **Actor**: authenticated human or credential that initiated a request, when one exists.
- **Producer**: registered server-side component that emitted the event.
- **Workspace**: tenant boundary to which the event and actor are bound.
- **Brand**: optional narrower scope, also bound into the signature.
- **Transport metadata**: source labels, process IDs, correlation IDs, and delivery receipts. Useful for diagnostics, never proof.

The producer capability is minted by trusted startup composition and injected only into its owning publisher adapter. The private signing key is likewise available only to that producer adapter. Consumers receive public verification keys and a key-ID-to-producer ownership policy. The registry must not be handed to ordinary runtime services.

The in-process capability is an application boundary, not a sandbox against arbitrary malicious Python code already executing in the same process. Private-key custody and injection are the cryptographic boundary.

## 3. Event write contract

Add a nullable `provenance_envelope JSONB` column to `public.events` in a future additive migration.

- Existing rows and legacy publishers remain NULL and untrusted.
- The envelope is stored in a dedicated column, never in `payload`.
- The signed fields bind event ID, event type, workspace ID, brand ID, canonical payload digest, producer identity, optional actor identity, signing key ID, algorithm/version, and signing time.
- The envelope contains no private key material, bearer token, or secret credential.
- The database is a transport/storage layer, not the trust authority.
- Do not create a producer-key table containing private keys. Public key distribution and revocation belong to a trusted configuration/secret-management path.

For `UnifiedEventBus`, signing must happen after the canonical event ID and normalized event scope are known, and before the persistence record is enqueued. The same immutable envelope must be used for the durable row and downstream dispatch context. Persistence acknowledgement must continue to mean the event row was actually written.

For `SharedEventBus`, the publisher adapter must obtain a signed envelope from its own producer-scoped signer and write it in the same event-row insert. A source service label alone must never mint or upgrade provenance.

## 4. Consumer and replay contract

Verification happens at the consumer boundary, immediately before a handler receives an event:

1. Read event row and dedicated envelope.
2. If envelope is absent, malformed, oversized, unknown-key, invalidly signed, or mismatched to event ID/type/workspace/brand/payload digest, set provenance to absent/untrusted.
3. Verify key ownership: key ID must be authorized for the signed producer ID.
4. Verify actor workspace equals event workspace when actor identity is present.
5. Deliver verified provenance out-of-band in an explicit immutable event context. Never inject it into the payload.
6. Re-verify on every replay; do not trust cached verification flags or serialized Python objects.
7. Legacy payload-only handlers retain their current signature and behavior, but cannot consume trusted provenance. Provenance-sensitive handlers must explicitly subscribe to the context-aware interface.

The same verifier and canonicalization rules must be used by UnifiedEventBus dispatch, SharedEventBus dispatch, dead-letter retry, and any future database replay. Handler receipts prove delivery progress, not producer identity.

A signature-verification failure must not drop an otherwise valid legacy event globally. It must deliver with provenance absent, emit a bounded security diagnostic, and ensure provenance-sensitive consumers fail closed.

## 5. Authenticity versus freshness (important correction)

Cryptographic authenticity and operational freshness are separate properties.

The current PR #86 prototype has a short envelope TTL. That is useful as a prototype freshness gate, but it is not sufficient as the long-term durable event contract: a historical event replayed after the TTL would lose verifiable provenance even though its original producer signature remains authentic.

The integration design must therefore:

- Verify that the signature was created by a key trusted for that producer at the signed time.
- Support key activation/retirement windows and explicit emergency revocation.
- Preserve historical signature authenticity after ordinary key rotation.
- Apply event-age / freshness policy separately at the consumer (for example, stale health observations may be authentic but ineligible to trigger a current goal).
- Treat emergency-revoked or compromised keys according to an explicit revocation policy, not by silently equating ordinary rotation with compromise.

Do not reuse a short-lived event freshness TTL as the cryptographic validity lifetime.

## 6. Key lifecycle and operational safety

Before any live producer is enabled, define:

- Per-producer key IDs and ownership.
- Private-key provisioning and rotation without exposing private keys to consumers.
- Public-key distribution to each verifier.
- Overlap during normal rotation.
- Emergency revocation and incident response.
- Startup failure behavior when a required producer key is absent or mismatched.
- Redaction rules so signatures, public keys, and provenance metadata do not leak secrets or sensitive actor data into logs.

No shared HMAC secret is used: a verifier with a shared HMAC key could also forge events. Ed25519 keeps private signing keys with producers and public verification keys with consumers.

## 7. Tenant-scoped health producer gate

Do not enable goal derivation merely because an event has a familiar name or source label.

A producer may become eligible only after it emits a workspace-bound observation backed by an authenticated tenant-specific source and the signed event is verified end to end. The current global `HealthMonitorService` is platform-scoped and must not be repurposed as tenant health. The existing Phase9/V69 observation contracts have no confirmed live tenant-health publisher call site yet.

Until a real tenant-scoped health producer is identified and integrated:

- `organism/event_bridge.py` remains at `source_trust=0.0`.
- No goal proposal is derived from legacy or unverified health events.
- Goal proposals remain `suggest` / `proposed`; no activation, remediation, or action execution is introduced.

## 8. Migration and rollout sequence

1. Finish and review PR #86's in-process capability and signature primitives.
2. Add an additive, nullable envelope column and disposable-PostgreSQL migration tests only. Do not apply to staging until separately authorized.
3. Add signer injection to UnifiedEventBus and SharedEventBus without changing legacy payload contracts.
4. Add one shared verifier and context-aware consumer interface; retain legacy handler compatibility.
5. Test signed and unsigned rows, payload/event/tenant substitution, invalid signatures, unknown keys, rotation, revocation, retries, dead letters, replay, and mixed old/new worker versions.
6. Add a real workspace-scoped health producer with evidence lineage.
7. Only then raise trust above zero for that specific producer and event family, behind explicit governance tests.

No main-branch promotion, production access, production DB changes, or deployment is part of this plan.

## 9. Acceptance gates

- Both event buses persist the same canonical envelope for the same event contract.
- Consumers independently verify every delivery and replay.
- No serialized `verified=true`, source label, process ID, event payload field, or receipt can confer trust.
- Workspace and brand substitution fail verification.
- Unsigned legacy events remain usable only as untrusted observations.
- Ordinary key rotation preserves historical authenticity; emergency revocation is tested.
- Freshness decisions are separate from signature authenticity.
- Tenant-scoped health evidence is independently authenticated and workspace-bound.
- The organism bridge remains fail-closed until every preceding gate passes.
