# Advanced Director — Lifecycle Ownership Implementation Proposal

**Status:** Design proposal; not approved for runtime implementation  
**Base:** `staging`  
**Scope:** Existing lifecycle manager, service container, service definitions, Director discovery and shutdown registry  
**Non-goals:** New lifecycle manager, new service registry, new event broker, topology graph rewrite, Guardian scans/remediation, content publishing, production changes

## 1. Executive decision

Use the existing `lifecycle.py` phased boot sequence and `shutdown_registry` as the sole process-lifecycle composition root. Add explicit owner steps only after process-mode ownership, dependency availability and shutdown semantics are verified. Keep topology/service-registry resolvers instance-only: diagnostic registration must never instantiate a lazy service or start work.

The current source audit establishes that `_build_boot_sequence(app, container)` selects steps from `SERVICE_MODE`, `boot_infrastructure` executes them in phase order, and `graceful_shutdown` delegates to `shutdown_registry.execute_all()`. It does not establish the effective mode of each deployed Render process.

## 2. Source baseline

The following findings were checked against the current `staging` branch:

| Source | Verified behavior | Consequence |
|---|---|---|
| `lifecycle.py` | Builds phased steps from `SERVICE_MODE`; runs each with timeout; tracks completed steps; graceful shutdown uses `shutdown_registry` | This remains the only lifecycle owner |
| `core/service_definitions.py` | Engagement resolver returns module singleton; EventRecorder resolver calls instance-only `_resolve_existing` | Registry reports only owner-created instances |
| `service_container.py` | `try_get_instance` returns existing singleton without invoking factory | Preserve this no-side-effect diagnostic boundary |
| `services/engagement_tracker.py` | Async module helpers create/start and stop/reset the singleton; class exposes async `start`/`stop` | Requires explicit mode owner and shutdown hook |
| `domain/analytics/event_recorder.py` | Constructor requires repository and bus; `start()` subscribes to five event types; no stop/unsubscribe method found | Not safe to wire until teardown is implemented and tested |
| `services/director/analysis.py` | Discovery tuple requests `services.guardian_service.guardian_service` | Export currently mismatches inspected module |
| `services/guardian_service.py` | Exposes `GuardianService` class; constructor requires several collaborators; scan can trigger external activity and optionally GitHub PR creation | Discovery/readiness must not construct-and-scan implicitly |

The Director's canonical dependency graph includes `condition_engine → engagement_tracker` and `guardian_scheduler → guardian`. These edges do not by themselves define optionality or process-mode ownership.

## 3. Ownership and implementation decisions

### 3.1 EngagementTracker

**Proposed owner:** the process role that owns Condition Engine's engagement-dependent behavior, subject to confirmation from the Condition Engine owner and deployed process configuration. Do not start it in every web/worker process by default.

Implementation shape after approval:
1. Place one explicit boot step after Supabase, EventBus and Condition Engine dependencies are initialized.
2. Call the existing `start_engagement_tracking(container, interval_minutes=...)` composition helper rather than constructing another tracker.
3. Ensure the helper's dependency resolution and the Condition Engine instance come from the same process composition root.
4. Register `shutdown_engagement_tracking` with `shutdown_registry` immediately after successful start.
5. Ensure failed/partial startup leaves no orphaned task and repeated startup is idempotent.
6. Publish lifecycle/readiness evidence separately from registration.

**Open owner decision:** is EngagementTracker required for Condition Engine readiness, or does Condition Engine have a tested degraded mode without it? Until resolved, do not weaken the canonical dependency edge merely to make topology complete.

### 3.2 EventRecorder

**Proposed owner:** the process role that owns analytics event persistence, only after its repository and event-bus dependencies are available.

Required implementation before adding a boot step:
1. Add explicit idempotent subscription lifecycle with retained subscription handles or a supported bus unsubscribe API.
2. Add `stop()` that unsubscribes exactly the handlers installed by `start()`, is safe when called repeatedly, and prevents a partially started recorder from leaking subscriptions.
3. Add a single owner boot function that obtains the existing AnalyticsRepository and the composition-root EventBus bridge, constructs exactly one recorder, registers it in the container, starts it, and then registers shutdown.
4. Keep `_resolve_event_recorder` instance-only.
5. Do not make topology reads call the constructor or `start()`.

If the current EventBus contract does not support unsubscribe, resolve that interface deliberately before implementation; do not simulate cleanup by replacing the global bus or creating a second bus.

### 3.3 Guardian identity and governance boundary

**Proposed owner:** one explicitly selected process role, likely a security/maintenance worker, pending confirmation.

Required sequence:
1. Inspect and reconcile the existing Guardian DI factory, Director discovery and any existing scan scheduler before choosing the canonical instance owner.
2. Make Director discovery resolve the exact same owned instance as DI; avoid module-level construction as an import side effect.
3. Split passive `get_status()` observation from active `scan_all()`.
4. Do not call `scan_all()`, scanner operations, auto-fix or GitHub actions from topology/readiness probes.
5. Keep remediation behind existing approval, authorization, evidence and idempotency gates. A Guardian boot step must not implicitly authorize remediation or PR creation.
6. Register explicit shutdown only if the service owns resources requiring it.

No Guardian instance should be created until all required collaborators and process role are identified.

### 3.4 GuardianScheduler

The canonical node and dependency edge exist, but the audited owner map does not identify a boot owner. Before implementation, search the full staging repository for scheduler implementations, aliases, task registrations and deployment entrypoints. Then choose one:
- map an existing implementation to one process-mode owner; or
- propose a separately reviewed canonical topology correction if the node is stale.

Do not create a new scheduler simply to satisfy the graph.

## 4. Boot and shutdown contract

Every future boot step must specify:
- allowed process modes and owner identity;
- dependency preconditions and exact existing instances it consumes;
- bounded timeout and startup evidence;
- idempotent behavior on repeated invocation;
- rollback for partial construction/subscription/task startup;
- shutdown hook registered immediately after successful acquisition;
- behavior when shutdown races with startup;
- whether absence in a process is `not_expected` or a startup failure.

Boot ordering must follow actual dependency readiness, not merely alphabetical order or topology registration order. A step must not claim `healthy` merely because its constructor returned.

## 5. Readiness and reporting

Keep these facts separate:
- declaration/manifest expectation;
- construction/registration;
- successful start;
- capability readiness from fresh evidence;
- authorization to perform an action.

Use the previously proposed states `not_expected`, `starting`, `healthy`, `degraded`, `unavailable`, `stale`, and `unknown`. The existing topology endpoint's `healthy` remains a structural topology result; do not silently repurpose it as runtime readiness.

## 6. Acceptance tests required before merge

1. Each process mode includes only its declared lifecycle owners.
2. EngagementTracker starts once, uses the existing container dependencies, and stops on graceful shutdown.
3. Repeated boot and concurrent boot do not create duplicate tracker tasks.
4. EngagementTracker partial startup failure cleans up the task and singleton state.
5. Condition Engine behavior is tested with tracker present and absent according to the approved dependency policy.
6. EventRecorder start is idempotent; stop removes all five subscriptions; start-stop-start works without duplicate delivery.
7. EventRecorder partial subscription failure rolls back earlier subscriptions.
8. Registry/topology inspection does not invoke any lazy factory, constructor, start method or external operation.
9. Guardian discovery and DI return the same instance identity; import/discovery has no scan or remediation side effect.
10. Guardian status probes are passive; no GitHub PR, notification, scan or auto-fix is triggered.
11. GuardianScheduler is mapped to an existing owner or the topology correction is separately reviewed.
12. Startup failure and shutdown order are tested, including rollback of already completed steps.
13. Web and worker processes report distinct instance identities; no assumption of cross-process singleton state.
14. No healthy/readiness state grants execution or remediation authority.

## 7. Required evidence before implementation approval

- Redacted startup records showing effective `SERVICE_MODE` and process role for staging web and worker.
- Full Guardian DI factory and scheduler implementation inventory.
- AnalyticsRepository construction owner and EventBus unsubscribe semantics.
- Condition Engine owner decision on EngagementTracker required/degraded behavior.
- Confirmation of shutdown registry idempotency and rollback ordering for these new hooks.
- Staging-only test plan and explicit approval for runtime changes.

## 8. Delivery boundary

This document is design-only. It does not authorize code changes, tests, migrations, database writes, credential changes, service restarts, deployments, merges or production operations. Any runtime implementation should be a separate, narrowly scoped PR, staging-first, with tests and verified logs before any deployment.


## 9. Follow-up source reconciliation (2026-11-11)

A deeper search of the current `staging` source refines the earlier inventory. These findings supersede any broad implication that Guardian or EventRecorder has no DI factory.

### 9.1 Guardian already has a lazy DI factory

`dependency_registry.py::_register_guardian` registers a singleton factory for the concrete `GuardianService` class when `settings.guardian_configured` is true. The factory builds GitHub client, scanner, auto-fix engine, EventBus and Facebook dependencies. The factory is lazy: registration alone does not construct the service.

The authenticated Guardian API routes resolve `GuardianService` through the container. The Director `ENGINE_REGISTRY` and Meta Engine's module-attribute discovery instead request `services.guardian_service.guardian_service`, which is not exported by the inspected module. This is an adapter/identity-discovery mismatch, not proof that the service class is absent.

**Revised proposal:** do not add a second Guardian factory or module singleton. Add a typed adapter that asks the existing composition-root container for `GuardianService`, and have Director/Meta discovery consume that same instance through an explicitly injected resolver. Keep the instance-only topology resolver separate so health/registration reads do not force the lazy factory. Confirm how the deployed process constructs its container before changing the adapter.

### 9.2 EventRecorder also has a lazy DI factory

`dependency_registry.py` registers a singleton factory for `EventRecorder`, resolving `AnalyticsRepository` and `UnifiedEventBus`. This means the class is constructible through the main dependency container. The separate Meta/service-registry resolver deliberately calls `try_get_instance`, so it will report no instance until an owner has explicitly resolved and started it.

The existing `EventBus.subscribe(event_type, handler)` returns an unsubscribe callable. EventRecorder currently discards those return values and has no `stop()` method.

**Revised proposal:** no new repository or bus adapter is needed merely to support teardown. Retain the five unsubscribe callables in EventRecorder, implement idempotent start/stop with rollback if a subscription fails partway through, and let one lifecycle owner resolve/start the existing factory. Add the shutdown hook immediately after successful start. Verify whether the actual runtime uses the legacy EventBus bridge or another bus implementation and test the exact type contract.

### 9.3 Guardian ScanScheduler exists but is not established as an active owner

`scheduler/scan_scheduler.py` defines a daemon-thread scheduler with `start()`, `stop()` and `status()`. Its run loop executes the callback immediately on startup, then repeats on the configured interval. Repository-wide code search found no `ScanScheduler(` construction call in the indexed source; this must be rechecked against a full checkout before treating that absence as conclusive.

**Security consequence:** do not wire this scheduler into generic boot. Its immediate callback invokes Guardian scanning. Any future owner must be an explicitly approved security/maintenance process, with the scan callback's external effects, auto-fix configuration, GitHub PR behavior, cancellation and shutdown timeout covered by policy and tests. The existing scheduler class is a candidate to reuse, not authorization to run it.

### 9.4 Updated component status

| Component | Corrected source status | Remaining work |
|---|---|---|
| GuardianService | Existing lazy DI factory; Director/Meta discovery expects wrong module attribute | Unify discovery through the existing container; no second factory |
| EventRecorder | Existing lazy DI factory; event bus returns unsubscribe callables | Retain handles, add safe stop/rollback, assign owner |
| ScanScheduler | Class exists; starts callback immediately; no active construction found in indexed search | Establish owner and explicit scan policy before any start |
| EngagementTracker | Existing lifecycle helper; no corresponding lifecycle boot step found | Confirm process-mode owner and dependency policy |

These corrections make the implementation target narrower: reuse existing factories, bus contracts and scheduler class; repair identity/lifecycle integration rather than duplicating services.
