# U2 producer signing-key custody and lifecycle design

Status: design only. This document does not create, provision, rotate, revoke, or deploy any key. It does not enable a producer or grant event trust.

## Scope and security objective

Define how a registered KDO event producer may sign durable event provenance without sharing private signing material with consumers, other producers, the database, or repository history.

The design applies to the Ed25519 envelope prototype in `core/durable_event_provenance.py` and the event transports under development in PRs #86–#89. It does not authorize production or staging activation.

## Identity model

Keep these identities separate:

- **Producer ID** identifies the server-side component that emitted the event.
- **Key ID** identifies one version of that producer's signing key. It is public metadata, not a credential.
- **Actor principal** identifies the authenticated human or service on whose behalf work was requested, when applicable.
- **Workspace ID** scopes the event to a tenant.
- **Event ID/type and payload digest** bind the signature to the exact event.

A key ID should use a stable KDO-style identifier, for example `KDO-KEY-TENANT-HEALTH-V1`. This is a label only. It must not be treated as a secret, API token, or proof of authorization. Private key material must retain full cryptographic entropy and must never be shortened or made human-readable to satisfy a naming convention.

## Key generation and private-key custody

1. Generate each Ed25519 private seed using a cryptographically secure random generator in a trusted provisioning environment. A private seed is exactly 32 random bytes.
2. Generate a distinct key for each producer identity and environment. Do not share one private key across producers, staging and production, or unrelated services.
3. Store each private key only in the secret configuration of the specific trusted producer runtime that needs to sign. A deployment platform's encrypted secret/environment-variable facility may be used when it provides suitable access controls. Do not place the key in source control, ordinary configuration files, event payloads, database rows, logs, build artifacts, client applications, or a shared general-purpose workspace record.
4. Inject the private key into a narrow signer adapter at startup. Do not expose it to the event bus, organism coordinator, goal derivation, consumers, or general application services.
5. The composition root should bind one producer identity to one signer and one producer capability. A component must not be able to request another producer's signer or mint arbitrary capabilities.
6. If the runtime cannot isolate a producer's secret from unrelated code in the same process, document that limitation. The current in-process registry is an application boundary, not a hostile-code sandbox. Do not claim hardware-backed isolation or per-process isolation unless actually implemented and verified.

No key is to be provisioned as part of this design-only change.

## Public verification-key distribution

- Consumers receive public keys only.
- Maintain an explicit mapping of `key_id -> producer_id`; the signed producer ID must match this mapping.
- The verification configuration must be versioned and reviewed. Unknown keys, missing ownership mappings, malformed keys, and retired or revoked keys fail closed.
- Do not load public keys or ownership mappings from event payloads or untrusted database fields.
- Public key configuration may be distributed to the consumers that need it. Do not distribute private signing material to consumers.
- Keep environment separation explicit: staging consumers must not silently trust production-only keys and vice versa.

## Rotation lifecycle

Use an overlap rotation:

1. Create a new key ID and private key for the same producer.
2. Add the new public key and ownership mapping to the intended consumers.
3. Verify consumer readiness before enabling the producer to sign with the new key.
4. Switch the producer signer to the new key ID.
5. Retain the previous public key for historical authenticity verification while it remains within the approved retention period and has not been revoked.
6. Remove the previous key from active verification only after the retention window and replay requirements have been reviewed.

Rotation must not change producer identity or workspace authorization. Key rotation alone does not grant a producer permission to publish new event types or create goal proposals.

## Revocation and compromise response

Revocation is distinct from routine rotation.

- Maintain an explicit revocation/deny policy for key IDs. A revoked key must not verify as trusted merely because its public key remains available for historical lookup.
- On suspected private-key compromise, stop signing with that key, revoke it at consumers, provision a replacement through the controlled process, and investigate the affected event interval.
- Do not assume an envelope's self-reported `issued_at` timestamp proves it was signed before compromise. A compromised producer can backdate a signed envelope. Historical verification after compromise therefore requires an incident-specific trust decision and independent evidence; it must not automatically restore operational trust.
- Preserve event records and verification outcomes for audit. Do not silently rewrite old provenance or delete events to hide a compromise.
- A revoked key may remain available in a forensic-only archive, but forensic authenticity results must be clearly labelled as compromised-key evidence and must not be accepted for current operational decisions.

## Authenticity versus freshness

The verifier's `enforce_freshness=False` mode is for historical authenticity checks only. It answers whether the retained public key verifies the signature and whether the signature binds to the supplied event.

It does not establish that:
- the health observation is still current;
- the producer was uncompromised at signing time;
- the event is eligible for new goal derivation;
- the workspace remains active;
- any action is authorized.

Operational consumers must separately enforce event age, expiry, workspace status, event-type allowlists, producer status, and current observation state. Historical replay must never refresh an expired health observation or create a new live goal solely because its signature verifies.

## Operational controls and audit evidence

Before any producer is enabled, record and review:

- producer ID and producer class;
- environment and owning service;
- key ID, public-key fingerprint, creation/activation/retirement timestamps;
- secret custody location and access boundary (never the secret value);
- key-owner mapping and consumer distribution version;
- rotation/revocation status and incident contact/owner;
- approved event names and workspace-binding source;
- tests proving cross-workspace rejection, producer/key mismatch rejection, unknown/revoked key rejection, payload tamper rejection, and stale-event rejection for operational use.

Never log private seeds, secret environment values, or complete secret-bearing configuration. CI must use ephemeral test-only keys and must not upload private keys as artifacts.

## Preconditions for a first tenant-scoped producer

A first live producer must not be selected merely because it has a convenient health class or event name. Before signing is enabled:

1. Identify a real tenant-owned dependency or connector lifecycle with a verified workspace binding and evidence reference.
2. Prove that the observation is generated from that tenant's own registry/connection state, not global platform health.
3. Give only that producer its capability and signer from trusted startup composition.
4. Publish through the canonical bus with the signed envelope persisted separately from payload data.
5. Verify the envelope independently at each durable/cross-process consumer boundary.
6. Keep legacy source-label publishers unverified.
7. Let the organism bridge raise source trust only for the exact registered producer, allowed event type, matching workspace, valid signature, active key, and fresh observation.
8. Keep derived goal proposals at `authority=suggest` and `status=proposed`. No automatic activation, remediation, or external action is included.

The existing global `HealthMonitorService` is not a tenant-scoped producer and must not be relabelled as one.

## Explicit non-goals

This design does not:
- create or store real keys;
- modify Render environment variables or secrets;
- modify Supabase or apply migration 158;
- enable a producer or raise `source_trust`;
- activate goal proposals or invoke OODA action execution;
- deploy, publish, or touch production.

## Acceptance gate

This design is complete only when a separately reviewed implementation demonstrates isolated per-producer key custody, explicit public-key ownership and revocation policy, successful overlap rotation, cross-process verification, and a real workspace-scoped producer. Until then, the organism event bridge remains at `source_trust=0.0`, and all goal proposals remain blocked from event sources without authenticated provenance.
