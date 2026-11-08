# U2 producer provenance audit and trust contract

Status: design/audit only. This document does not enable goal proposals, change runtime trust, apply migrations, or authorize actions.

## Evidence reviewed

The audit was performed against the U2 runtime-goal-proposal integration branch after its reconciled CI run.

1. `core/unified_bus.py` exposes `UnifiedEventBus.publish(..., source: str = "unknown")`. The source is a caller-supplied label. The event envelope persists that label as source metadata; the bus does not authenticate the publisher.
2. `EventMetadata` and `EventStoreEntry` retain event/source/workspace metadata, but no authenticated producer principal or verified producer credential.
3. `organism/event_bridge.py` receives the event envelope and converts selected events into observations. Its current trust assignment is deliberately `source_trust=0.0`; the source label remains useful for diagnostics but cannot qualify an event for goal derivation.
4. `core/workspace_context.py` binds workspace scope after authentication. Its current `WorkspaceContext` includes workspace, brand, resource references, species flag and authentication method, but no stable authenticated subject/principal identifier.
5. `core/species_auth.py` resolves an `AuthIdentity` containing method, workspace, role, user ID, permissions and species status. In `api/dependencies.py`, `require_auth` currently stores only authentication method, workspace ID and user email in context variables, then returns a boolean. The richer identity is not carried forward as a first-class event actor.
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
