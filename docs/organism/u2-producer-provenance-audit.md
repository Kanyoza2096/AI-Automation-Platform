# U2 producer provenance audit and trust contract

Status: design/audit only. This document does not enable goal proposals, change runtime trust, apply migrations, or authorize actions.

## Evidence reviewed

The audit was performed against the U2 runtime-goal-proposal integration branch after its reconciled CI run.

1. `core/unified_bus.py` exposes `UnifiedEventBus.publish(..., source: str = "unknown")`. The source is a caller-supplied label. The event envelope persists that label as source metadata; the bus does not authenticate the publisher.
2. `EventMetadata` and `EventStoreEntry` retain event/source/workspace metadata, but no authenticated producer principal or verified producer credential.
3. `organism/event_bridge.py` receives the event envelope and converts selected events into observations. Its current trust assignment is deliberately `source_trust=0.0`; the source label remains useful for diagnostics but cannot qualify an event for goal derivation.
4. `core/workspace_context.py` binds workspace scope after authentication. Its current `WorkspaceContext` includes workspace, brand, resource references, species flag and authentication method, but no stable authenticated subject/principal identifier.
5. `core/species_auth.py` resolves an `AuthIdentity` containing method, workspace, role, user ID, permissions and species status. At the time of the initial audit, `require_auth` stored only authentication method, workspace ID and user email in context variables. The current draft branch now corrects that gap: after successful `SpeciesAuth.validate()`, the canonical `require_auth` yield dependency binds the validated `AuthIdentity` to request-scoped `auth_identity_var` and resets it with the other auth ContextVars in `finally`. `current_actor_principal()` derives the actor only from that validated context. This establishes request-scoped actor attribution; it does not authenticate the internal event producer.
6. Workspace identity and producer identity are different facts. A valid workspace binding proves tenant scope; it does not prove which internal component emitted a particular event.

## Security conclusion

An allowlisted event name plus a matching `source` string is not authenticated provenance. A producer can claim another producer's label unless the publishing path obtains identity from trusted server-side composition. Assigning a fixed confidence or trust score to such a label would create a false trust signal.

Therefore:
- Keep bridged event observations at `source_trust=0.0` until verified producer identity exists.
- Do not accept `is_trusted`, `source_trust`, producer IDs, signatures, or authority claims from event payloads or public request fields.
- Do not infer producer trust from workspace ID, event name, source string, correlation ID, or event type alone.
- Do not activate, approve, execute, or remediate from a goal proposal. Producer trust is provenance evidence, not execution authority.

## Required provenance model

The eventual contract must represent at least two separate identities:

- **Actor principal**: the authenticated human or service on whose behalf work was requested. For HTTP-originated work this must derive from the validated `AuthIdentity`, not request headers or payload fields.
- **Producer principal**: the server-side component that actually emitted the event. It must derive from a trusted publisher capability registered by the composition root or worker bootstrap, not from the `source` string passed by arbitrary callers.

A verified provenance record should bind:
- stable producer principal ID and producer class;
- actor principal ID/method when applicable (nullable for autonomous internal events);
- workspace ID and optional brand ID;
- event ID and event type;
- issue/creation time and schema version;
- a verification result tied to the publishing boundary.

For asynchronous work, provenance must cross the queue through a server-authenticated internal envelope or be re-established by a trusted worker from a validated job record. A plain serialized boolean or caller-supplied identity is insufficient.

## Implementation sequence and acceptance gates

1. Preserve the validated `AuthIdentity` as request-scoped actor context, including stable subject ID, method, role and workspace scope. Ensure context is cleared at request/task boundaries and cannot leak across concurrent requests.
2. Introduce a server-side producer registry/capability at the composition root. Only registered internal components receive publisher handles. Keep the human actor and internal producer as separate fields.
3. Add a versioned provenance envelope to event publishing/persistence. Legacy publishers without a verified handle remain explicitly unverified; do not silently promote their source labels.
4. Propagate and verify provenance through durable queue/replay paths. Reject mismatched workspace, event ID, producer registration, or invalid signatures/capabilities.
5. Update the organism bridge to derive trust only from a verified provenance result and a narrow producer allowlist. Keep unknown/legacy events useful for perception but ineligible for goal derivation.
6. Add negative tests for forged source labels, forged payload trust flags, cross-workspace provenance, unregistered publishers, replay/tampering, context leakage, and legacy events. Add positive tests for registered producers and authenticated actor attribution.
7. Only after those tests and independent review may a separate change raise producer trust above zero. Goal proposals must still remain `suggest` / `proposed`, and all action execution remains behind existing governance and human authorization.

## Current gate

**Blocked by missing authenticated producer provenance.** The present implementation intentionally fails closed. Migration 157 remains unapplied to staging; PRs #83–#85 remain drafts; no Render deployment, production access, database mutation, publishing, or external action was performed as part of this audit.


## Contract prototype added (not yet wired into publishing)

The branch now contains `core/event_provenance.py` and `tests/test_event_provenance.py`.

- `ActorPrincipal.from_auth_identity()` derives actor classification and subject only from an already validated `AuthIdentity`. It deliberately leaves subject ID unset for credential classes whose stable identifier is not currently available.
- `ProducerRegistry` models a composition-root-owned registry. It mints opaque in-process `ProducerCapability` objects for explicitly registered producer IDs.
- `verify()` rejects arbitrary strings/dictionaries, capabilities issued by another registry, unregistered producers, missing workspace scope, and actor/event workspace mismatches.
- `VerifiedEventProvenance` is an in-process result. It is not a signature and must not be accepted merely because a serialized object claims `verification=composition_root_capability`.
- The registry and issuance method must remain confined to trusted startup composition. Runtime components receive only their own publisher capability. This is an application architecture boundary, not a sandbox against malicious code already executing inside the Python process.

This is a tested contract prototype only. It is not yet connected to `UnifiedEventBus.publish()`, request ContextVars, durable event storage, or queue replay. Until those integrations are implemented and adversarially tested, the event bridge remains at `source_trust=0.0` and goal proposals remain blocked.


## Authenticated request actor context implementation

The current branch now adds `auth_identity_var` to the canonical `api.support` context module. The canonical `require_auth` dependency sets it only after `SpeciesAuth.validate()` returns a valid `AuthIdentity`, then yields control to FastAPI and resets the identity plus the existing auth method/workspace/email ContextVars in a `finally` block.

`core.event_provenance.current_actor_principal()` reads only this validated context and converts it to the actor model. Credential types without a stable subject ID remain subject-less rather than inventing an identifier.

Regression coverage now exercises:
- validated identity is visible during the dependency lifetime;
- identity and auth ContextVars are reset after normal dependency cleanup;
- cleanup still happens when endpoint execution raises;
- actor principal lookup returns no identity outside the authenticated context and rejects invalid identities.

The request actor path is now implemented as a request-scoped context boundary. It does not yet alter event publishing or event persistence. In particular, it does not prove which internal component emitted an event. The producer capability remains a separate, not-yet-integrated requirement, and the organism event bridge continues to assign `source_trust=0.0`.


## Follow-up transport and health-producer audit (2026-11-09)

### Event transport boundary

The repository has two relevant event transports:

- `core/unified_bus.py` is the canonical in-process async event bus. It persists `events` rows, dispatches an envelope to subscribers, and supports in-memory dead-letter replay.
- `core/shared_event_bus.py` is a database-backed cross-process transport. Its `source_service` and `source_instance` values are labels/diagnostic process identifiers, not authenticated producer identities.

Repository searches found legacy publishers across Director, MetaEngine, ConditionEngine, plugins and other services. These call the bus with ordinary `source=` strings. The source string is caller-supplied. The Nerve envelope preserves it as metadata but does not authenticate it. The event table currently stores `source_service` and `source_instance`; neither is a producer credential.

The current `UnifiedEventBus` dispatch envelope contains event name, payload, source, event ID, workspace, brand, decision, correlation and parent-event IDs. `EventStoreEntry` and the persistence writer do not carry actor or verified producer provenance. Therefore, adding a capability check only inside the organism bridge would be too late: the bridge receives no verifiable producer identity. Adding a capability check only to the new producer prototype would also be insufficient unless the canonical bus and every durable transport preserve the result.

### Workspace-scoped health source audit

Two existing models have useful workspace-scoped health contracts:

- `services/phase9_reliability.py`: `DependencyHealthObservation` binds workspace ID, dependency ID, state, timestamp, evidence type/reference and explicitly carries `authority="none"`.
- `services/v69_connector_health_control.py`: `HealthObservation` binds workspace, connection, connector, state, timestamp and evidence reference; `assess_health()` checks these against the matching connection and connector registry record.

However, repository-wide call-site searches found no production publisher/constructor path for `DependencyHealthObservation`, and no production call site for the V69 health-control orchestrator beyond its module and tests. These are data/control contracts, not currently live tenant-health producers.

The global `HealthMonitorService` is not an acceptable substitute. Its lifecycle boot path constructs it without an event bus, its event bus setter is not wired, and its `health.state_changed` event has no tenant workspace binding. It reports process/platform health, not a particular tenant's dependency health.

### Decision

Do not create a new generic health event by relabelling platform health as tenant health. The next implementation must first define and wire a real workspace-scoped observation producer against an existing tenant-owned dependency/connector lifecycle. The producer must receive a capability only from trusted composition and publish through the canonical bus boundary.

The bus-level change must then:
1. carry the in-process `VerifiedEventProvenance` object separately from payload data through event creation and dispatch;
2. persist a versioned, structured provenance record without putting a forgeable trust flag in the payload;
3. define how durable replay verifies authenticity after process restart (a serialized dataclass or boolean is not verification);
4. keep events written through legacy publisher paths unverified;
5. ensure the cross-process shared bus cannot upgrade a source label into trusted producer identity;
6. let the organism bridge assign positive source trust only to a verified, workspace-matching producer and event contract.

Until durable replay verification exists, even an event verified only in memory must not be upgraded when reconstructed from an unsigned database row. This audit does not change runtime trust: `organism/event_bridge.py` remains at `source_trust=0.0`.

### Scope and safety

This follow-up is repository inspection and design clarification only. No migration was applied, no runtime publisher was enabled, no event was emitted, and no staging or production database/service was touched. Migration 157 remains unapplied; PRs #83–#85 remain drafts.


## Live Unified Event Bus transport prototype (2026-11-09)

A draft implementation is now available on PR #86 (not merged or deployed):

- `UnifiedEventBus` accepts an optional `ProducerRegistry` injected at construction by a future trusted composition root.
- `publish(..., producer_capability=...)` verifies the opaque capability against the event's normalized workspace and the current validated actor context.
- Verified provenance is attached to the in-process dispatch tuple and exposed to handlers as a separate `provenance` field. It is never copied into the payload.
- Legacy source labels produce `provenance=None`. A forged string/object, or a capability supplied when the bus has no registry, is rejected.
- If an actor is present, its workspace must exactly match the event workspace; a missing actor workspace is not accepted as a match.

This prototype deliberately does **not** upgrade trust for durable or cross-process events:

- The event persistence row does not contain a verifiable provenance proof.
- Database replay and dead-letter replay do not regain provenance.
- `SharedEventBus` remains unverified; source_service/source_instance remain diagnostic labels.
- The organism event bridge remains `source_trust=0.0`.
- No real health publisher has yet been issued a capability.

The prototype is an in-process composition boundary, not a sandbox against malicious code already executing inside the process. Before any positive trust is granted, the next stages must establish a trusted composition-root registry, signed/versioned durable provenance with key lifecycle and replay verification, a verified cross-process transport contract, and a real workspace-scoped producer with independent tests. Until then, all existing event sources remain untrusted for goal derivation.
