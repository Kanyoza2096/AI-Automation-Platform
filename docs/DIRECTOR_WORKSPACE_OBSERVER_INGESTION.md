# Director workspace observer ingestion — security design

## Status

Design only. No workspace observation-ingestion route is enabled.

Workspace observations can currently be restored from persisted rows whose `scope_type` is `workspace` and whose `workspace_id` exactly matches the trusted request workspace. The built-in Director observer loop is platform-owned: it invokes process-registered observers and persists platform observations. It is not a tenant observer ingress mechanism.

## Why a generic authenticated endpoint is not safe

The current authenticated `RequestContext` establishes a principal, workspace, role, permissions, principal type, credential purpose and resource constraints. It does not, by itself, establish that a principal is authorized to assert a particular observer identity or sector. Accepting `source`, `observer_id`, `workspace_id`, or `sector` directly from a request body would let an ordinary workspace user forge evidence. A valid workspace session is not evidence-source authority.

The repository does not currently expose a canonical observer-principal registry or an observer-specific permission/credential contract. Therefore a generic POST endpoint must not be added until that missing authority binding exists.

## Required authority contract

Before enabling ingress, provide a server-controlled observer registration that binds:

- an immutable observer principal / credential identity;
- one exact workspace ID (or an explicitly platform-scoped observer);
- the allowed observer ID and sector;
- the permitted contract version and metric namespace;
- credential purpose `director_observer` and machine principal type;
- active/revoked state, expiry and rotation metadata.

The registration must be resolved server-side after canonical authentication. The caller must not choose or override the workspace, observer ID, sector, scope type, or authority. A platform observer must never be re-labelled as workspace evidence.

## Proposed request flow

1. Canonical middleware validates the machine credential and binds `RequestContext`.
2. Ingress requires a valid identity with `principal_type == "machine"`, the dedicated observer credential purpose, and an active server-side registration.
3. The registration's workspace and observer ID must match the trusted context and route binding exactly. Body-supplied workspace/source values are rejected, not trusted.
4. Validate a versioned, bounded observer payload: allowed health states, registered sector, timestamp freshness/future-skew, metric depth/size/types, issue count/size, and contract version.
5. Reject stale, malformed, unknown, conflicting, revoked, or out-of-scope evidence. Preserve provenance (observer registration ID, principal, contract version, received-at, observed-at, correlation ID, payload digest).
6. Construct the `Observation` server-side with `scope_type="workspace"` and `workspace_id` taken only from the trusted registration/context.
7. Persist through the existing scoped store contract. Update only `workspace_observations[workspace_id]`; never mutate `self.observations`, platform topology, platform diagnostics, or platform learning.
8. Do not execute capabilities or admit missions from ingestion. Any later recommendation/admission remains advisory and passes through existing governed approval.
9. Add replay/idempotency controls and an audit trail. Replays must not refresh evidence freshness or silently replace provenance.

## Required regression tests

- missing/invalid auth identity and missing RequestContext are rejected;
- human workspace sessions cannot submit observer evidence;
- machine credentials with the wrong purpose are rejected;
- unknown, revoked, expired, or wrong-workspace observer registrations are rejected;
- body workspace/source/sector spoofing is rejected;
- one workspace cannot write another workspace's observer registry;
- platform observer credentials cannot be downgraded to tenant evidence and tenant observers cannot write platform scope;
- stale/future timestamps, malformed contracts, oversized payloads and unsupported metric shapes are rejected;
- duplicate deliveries are idempotent and cannot extend observed-at freshness;
- accepted evidence is visible only in its exact workspace registry and is never visible in platform telemetry;
- ingestion never triggers execution, proposal admission, or platform learning.

## Implementation gate

Do not expose the endpoint until the observer registration/credential authority is implemented and reviewed. Until then, keep workspace verification fail-closed/unknown when scoped evidence is absent. This is safer than accepting evidence whose origin cannot be authenticated.
