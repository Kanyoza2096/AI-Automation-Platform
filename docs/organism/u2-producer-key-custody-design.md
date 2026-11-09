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


## Repository audit: tenant-scoped connector health candidate (2026-11-09)

A read-only code search and inspection identified the V69 connector lifecycle as a possible future source, but not yet a live event producer:

- `services/v69_connector_health_control.py` defines `HealthObservation` with workspace ID, connection ID, connector key, observation timestamp, health state, drift and evidence reference. `assess_health()` verifies that the observation's workspace, connection and connector match both the connection and registry record, and fails closed on tenant mismatch.
- `services/v69_integration_lifecycle.py` composes that assessment through `IntegrationLifecycleOrchestrator.health_control()`. It can emit an evidence record through an injected evidence sink and derive a downgraded connection/registry state.
- Repository-wide searches found no production composition-root instantiation of `IntegrationLifecycleOrchestrator`; the visible construction/use sites are definitions, tests and reference certification paths. The health observation factory also accepts supplied state/evidence values; it is not itself proof that a real provider check occurred.

Therefore V69 is a **candidate contract**, not evidence of an operational tenant-health producer. Before using it, the implementation must identify the authenticated connector runtime that performs the actual health check, verify that it owns the workspace-bound connection/registry, and attach a traceable provider response/evidence reference. Only then can the trusted composition root issue a producer capability to that runtime. Do not sign synthetic/reference observations or user-supplied health claims as if they were verified provider telemetry.

This audit did not instantiate the orchestrator, call any provider, publish an event, or touch a database or service.


## Additional audit: durable connector-health evidence is read-only today (2026-11-09)

The repository already contains a durable evidence table and a read model, but that is not yet a producer path:

- `migrations/100_vnext_connector_health_evidence.sql` defines `connector_health_observations`, scoped by `workspace_id`, `connection_id`, and `connector_key`, with observed time, state, drift, provider status code, latency and evidence reference. Its stated purpose is evidence/eligibility input only, never execution authority.
- `services/vnext_connector_fabric_evidence_read_model.py` reads the latest row for a workspace and connector. It exposes `authority=none`, `readOnly=true`, and `executionEffect=none`.
- A repository-wide search for the table name, health-observation persistence functions and likely writer entry points found the read model and migration, but no application writer for `connector_health_observations`. The existing V69 health-control contract is in-memory and its lifecycle module explicitly says it does not authenticate, store secrets, or execute provider side effects.
- `services/v69_reference_connector_certification.py` explicitly performs offline structural certification and states it does not contact Meta. It must not be treated as live provider health evidence.
- The read model's `_latest()` helper catches query errors and returns `None`; therefore an absent row currently cannot distinguish “no observation exists” from “the evidence query failed”. A future producer/consumer path must preserve that distinction rather than treating a read failure as a healthy or empty result.

### Consequence

The first producer implementation is not just “wire V69 into the event bus”. It needs a real, authenticated connector-runtime health check, a tenant-bound evidence writer, and an explicit event publisher. The writer must verify the connection/registry binding, require a provider-derived result and traceable evidence reference, and fail visibly on persistence errors. It must not accept a caller-provided status as proof that the provider was checked.

The durable evidence row can be the source record for a signed event, but it is not itself authenticated producer provenance. The signing capability must be held by the runtime that performed the provider check, and the consumer must verify the envelope independently.

This is a repository-source audit only. No Supabase query, migration, provider API call, credential access, event publication or deployment was performed.


## Runtime connector gateway audit (2026-11-09)

A further source audit found `services/v69_connector_runtime_gateway.py`, which is a useful tenant-bound execution facade: its constructor checks workspace, connection, registry, connector and governed-adapter alignment. Its `health()` method delegates to the governed connector. However:

- Repository search found the `RuntimeConnectorGateway` constructor in its tests, but no production composition-root instantiation.
- The Facebook reference certification path uses an offline fake service and explicitly does not call Meta.
- `FacebookPlugin.health(account)` can make a provider GET when passed a specific account, but the plugin's generic `health_check()` path calls `health()` without an account; the global reliability collector calls `plugin.health()` without a tenant account. That generic path is not a tenant-bound health producer.
- The tenant-specific account lookup is brand-scoped through `SocialAccountRepository.find_by_brand_and_platform`, but no current call path was found that combines that authenticated account lookup, a real provider health probe, a verified workspace-to-brand relationship, durable observation persistence and a signed event publication.

Therefore neither the V69 gateway nor the generic plugin health collector is currently sufficient to receive a producer signing capability. The future composition root must bind the authenticated workspace, brand, social account, connector contract and governed adapter together, then call a read-only provider health probe and persist its evidence before publishing a signed event. It must not reuse the global plugin health result or offline reference certification.

This was static repository inspection only. No plugin was started, no social account or token was read, and no Meta request or other provider call was made.
