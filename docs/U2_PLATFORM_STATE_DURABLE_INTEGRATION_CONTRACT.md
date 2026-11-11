# U2 Platform State — Durable Integration Contract

**Status:** design contract for the next implementation increment  
**Depends on:** Platform State model/store and HealthMonitor adapter in PR #158  
**Scope:** staging implementation only; this document grants no deployment, migration, secret, or production authority.

## 1. Purpose and authority

Platform State is the versioned, cross-engine operational projection of service and capability readiness. It is not a replacement for subsystem-owned records:

- HealthMonitor / health providers own health observations.
- `core/engine_topology.py` remains the canonical dependency graph.
- `core/service_registry.py` remains the live-instance registry; registration is not readiness.
- Director, Condition Engine and Organism consume projections; they do not write health facts on behalf of another service.
- Council and existing action-governance paths remain authoritative for sensitive actions. Platform State grants no execution authority.

The snapshot is a derived truth-plane view. It must retain evidence references and timestamps so consumers can inspect why a state was derived.

## 2. Process and transport boundary

The repository has two distinct event transports with different ownership:

1. `UnifiedEventBus` is the in-process async bus used by the FastAPI/API process.
2. `SharedEventBus` is the Supabase-backed cross-process transport for service-mode workers. Its module contract explicitly says not to use it inside FastAPI for domain events.

The Platform State publisher must therefore publish exactly once through the existing API-process `UnifiedEventBus`. That bus already persists events into the shared `events` table, which the worker-side `SharedEventBus` consumes. Do not dual-publish the same state transition to both transports; doing so would create duplicate durable events.

The cross-process event name is `platform.state.updated`. Its versioned payload contains only:
- `schema_version`
- `scope_type` and `scope_id`
- `revision`
- `updated_at`
- `snapshot_digest`
- `changed_service_ids`
- `changed_capability_ids`

The event is a notification, not the state payload of record. Consumers load the authoritative snapshot from the store and verify that its revision is at least the event revision. This keeps events small and avoids stale event payloads becoming an alternate state database.

## 3. Durable write protocol

The publisher performs the following bounded sequence:

1. Obtain the latest HealthRegistry snapshot through the existing cached-read API. It must not start additional provider probes.
2. Convert only recognized provider reports into owner-attributed observations.
3. Derive readiness using the Platform State model and canonical dependency graph.
4. Load the current persisted snapshot and revision.
5. If the derived state is materially unchanged, do not write or publish.
6. Save revision N+1 through `PlatformStateStore.save`, with expected revision N. The database RPC is the sole write path.
7. After a successful durable write, publish `platform.state.updated` through UnifiedEventBus.
8. On revision conflict, reload and recompute from the newest snapshot; bound retries. Never overwrite a newer revision.
9. If persistence fails, do not publish an update event. Report the projection as unavailable/stale to local diagnostics rather than implying cross-process freshness.

A failed event publication after a successful state write is recoverable: consumers can poll/load the current snapshot, and a later reconciliation can republish the latest revision. The publisher must not roll back a valid newer snapshot merely because event notification failed.

## 4. Idempotency, change detection, and freshness

- Snapshot revision increases only after a successful compare-and-swap write.
- A repeated health cycle with the same normalized service/capability facts must not create a new revision.
- Evidence timestamps and source versions participate in change detection; volatile process-local fields, probe durations, and arbitrary metadata do not.
- Staleness is derived from observation time and policy, not from whether a service is registered.
- Future-dated or malformed observations fail closed to UNKNOWN and are never upgraded to HEALTHY.
- The initial snapshot is revision 1, written only with expected revision 0.
- Missing rows cannot be recreated at a caller-selected non-zero revision.

## 5. Read API and consumer contract

Expose a read-only authenticated projection, scoped to the platform operator identity. A tenant/workspace credential must not gain platform-wide visibility merely by supplying `scope_type=platform`.

The response should include:
- scope and snapshot revision;
- snapshot update time and schema version;
- per-service readiness, owner, criticality, dependency list, evidence references and reason;
- derived capability readiness;
- freshness / availability metadata.

The API must not expose provider secrets, raw credentials, internal connection strings, or unfiltered provider metadata. It must distinguish:
- no persisted snapshot: `unknown` / `not_initialized`;
- stale snapshot: `stale`;
- database unavailable: `unavailable`;
- valid current snapshot: return its persisted revision.

Director, Condition Engine and Organism should receive a typed read-only interface, not direct Supabase table access. Their local caches, if any, must be revision-tagged and disposable. They must never infer HEALTHY from a missing record or local service registration.

## 6. Ownership and service manifest gap

The current service definitions provide service identity, resolver and criticality, but they are not a complete organizational ownership manifest. Do not invent team/service owners from naming conventions.

Until an explicit manifest exists, only services with a reviewed, explicit owner mapping may emit accepted observations. Unmapped services remain UNKNOWN and are surfaced as manifest gaps. The canonical dependency graph remains the sole dependency source; do not create a second graph in the manifest.

## 7. Lifecycle and process ownership

- Only the API process that owns HealthMonitor should build and persist the platform-wide snapshot.
- Worker processes consume the durable update event and read the snapshot; they do not run competing HealthMonitor writers.
- Register the publisher through the existing lifecycle/shutdown registry. Start and stop must be idempotent.
- The publisher must not run inside the Director decision loop and must not trigger content generation, publishing, remediation, deployment, or external side effects.
- If the health monitor is absent, no synthetic healthy snapshot is produced.

## 8. Required verification before merge or migration

Unit tests:
- no-op unchanged snapshot creates no revision/event;
- successful CAS increments exactly once;
- concurrent writers cause conflict/reload, never lost update;
- failed persistence emits no event;
- event failure leaves durable state intact and is recoverable;
- duplicate/replayed update event is harmless;
- stale, future, unknown and malformed observations fail closed;
- unauthorized platform-scope read is denied;
- tenant identity cannot query platform scope;
- API route redacts sensitive metadata;
- worker consumes notification and loads the persisted revision;
- lifecycle start/stop is idempotent.

Integration tests:
- apply migration 166 to an ephemeral PostgreSQL database;
- first insert at expected revision 0 succeeds;
- wrong initial revision and stale revision updates fail;
- RLS and grants prevent direct table writes and unauthorized reads;
- API-process UnifiedEventBus persists one notification that worker SharedEventBus can consume;
- restart/replay still yields the newest persisted snapshot.

Operational gate:
- no migration is applied to KDO staging until the exact migration file has been reviewed and ephemeral PostgreSQL tests pass;
- no Render restart/deploy until code is merged and staging migration is verified;
- production remains untouched.

## 9. Explicit non-goals

This increment does not:
- activate Facebook tenant health provenance;
- provision signing keys or readiness secrets;
- add a new broker, event table, registry, or dependency graph;
- create a new paid database/service;
- authorize autonomous remediation, deployment, or production writes;
- optimize heartbeat event volume.
