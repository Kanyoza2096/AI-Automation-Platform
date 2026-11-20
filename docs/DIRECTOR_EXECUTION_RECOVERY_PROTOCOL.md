# Director Execution Recovery Protocol

**Status:** Recovery contract / implementation design  
**Branch:** `feat/director-orchestration-architecture`  
**Scope:** Native Director mission-task execution only  
**Safety posture:** Fail closed; no automatic engine replay

## 1. Problem statement

The current execution path commits `task.started` and synchronizes the canonical `director_tasks` row to `executing` before invoking the registered native engine adapter. This correctly prevents a concurrent second request from dispatching the same task.

A process interruption can nevertheless occur after the durable start transition and before a terminal event is committed. The task then remains `executing`. The event journal proves that an execution attempt was admitted, but does not by itself prove whether the adapter was invoked or whether its side effects completed.

A timeout, lost HTTP response, or stale timestamp is not proof that no side effect occurred.

## 2. Required recovery classifications

Recovery assessment must be read-only and classify each attempt into exactly one of these outcomes:

| Classification | Required evidence | Permitted next action |
| --- | --- | --- |
| `not_started` | Authoritative proof that dispatch never began; no adapter invocation or side effect | A new governed attempt may be considered |
| `outcome_unknown` | Start was committed, but no authoritative terminal outcome exists | Hold execution; reconcile with the engine/operator |
| `completed_response_lost` | Authoritative engine result/evidence proves completion, but the caller did not receive or persist the response | Record/reconcile the existing result; never invoke the engine again |
| `failed_reconsiderable` | Authoritative failure evidence proves the attempt ended and establishes that no consequential side effect remains | Return to approval/review; a fresh governed approval and new attempt identity are required |

If evidence is incomplete, contradictory, stale, or not attributable to the same attempt, classify as `outcome_unknown`.

## 3. Identity and evidence contract

Every execution attempt must retain:
- mission, workspace, task, assigned engine and capability;
- immutable attempt ID and idempotency identity;
- initiating operator ID from the signed Gateway context;
- correlation ID and start timestamp;
- dispatch state and adapter invocation receipt, when available;
- terminal outcome, bounded result/evidence references, and outcome timestamp;
- recovery assessment, evidence references, assessor, and governed decision.

Attempt identity must not be reused for a later attempt. A retry is a new attempt, not a replay of the old request under a new prefix.

Evidence must be scoped to the same workspace, mission, task, engine, and attempt. A generic engine health check or elapsed-time threshold is not proof of no side effects.

## 4. State and transition invariants

1. A durable start claim is committed before adapter invocation.
2. At most one active attempt may own a task at a time.
3. Stale detection only raises a recovery candidate; it never changes an executing task to approved or dispatches an adapter.
4. `outcome_unknown` is non-retryable.
5. A completed attempt is reconciled from its durable result; it is never re-executed because an HTTP response was lost.
6. A failed attempt can be reconsidered only with authoritative evidence that the prior attempt ended and no unsafe side effect remains.
7. Reconsideration returns through the existing operator approval boundary. Recovery itself cannot grant execution authority.
8. Every recovery assessment and decision is append-only, idempotent, workspace-scoped, and attributable.
9. A conflicting or unavailable canonical task/event read fails closed.
10. Collaboration, Director recommendations, and engine-to-engine messages remain advisory and cannot approve or execute recovery.

## 5. Recovery workflow

1. **Detect:** A bounded read-only scan identifies tasks whose canonical status is `executing` and whose latest start attempt exceeds the configured stale threshold.
2. **Assess:** Load the canonical task and complete relevant event history. Correlate by attempt identity, not merely task ID or timestamp.
3. **Reconcile:** Query only an authoritative adapter/engine operation-status contract, where one exists. Do not infer completion from a generic response or an absent record.
4. **Classify:** Emit one of the four classifications. Missing or conflicting evidence becomes `outcome_unknown`.
5. **Govern:** Present evidence and recommended next action to an authorized human operator. The decision is separately persisted and audited.
6. **Resolve:** Reconcile an already completed result, keep an unknown attempt held, or return a proven no-effect failure to the existing approval-required path. Any new execution uses a new attempt ID and fresh approval.
7. **Verify:** Re-read the canonical task and event journal after the transition. If the read-back does not match, report a conflict and do not dispatch.

## 6. Implementation sequence

### Phase A — Read-only assessment
- Add a workspace-scoped recovery assessment over canonical task rows and mission events.
- Expose stale candidates and evidence-backed classifications without mutating task state.
- Add tests for all four classifications, missing evidence, contradictory events, workspace isolation, and completed-response-loss behavior.

### Phase B — Durable attempt ledger
- Design an additive, append-only attempt record and atomic claim/transition contract.
- Enforce one active attempt per task and unique attempt/idempotency identities in the database.
- Restrict writes to the backend service role; keep the table unavailable to public client roles.
- Review migration and RPC security before applying anything to the KDO production database.

### Phase C — Authoritative engine reconciliation
- Add an explicit status/result lookup contract only for adapters that can prove outcomes by attempt ID.
- Adapters without authoritative reconciliation remain `outcome_unknown` after interruption.
- Never synthesize a no-side-effect assertion from timeout or connection failure.

### Phase D — Governed resolution
- Add operator review and audited recovery decisions through the existing signed Gateway command boundary.
- Reuse existing task approval controls; recovery cannot bypass approval.
- Add concurrency, crash-window, replay, response-loss, and idempotency regression tests.

## 7. Non-goals and release gates

- No automatic retry of an interrupted native engine call.
- No assumption that Gateway's command transaction store is the authoritative engine execution ledger.
- No new production table, RPC, migration, or runtime flag activation as part of this design-only step.
- No production test data.
- No merge, deployment, or CI retry until the recovery contract and integration tests are reviewed and certified.

**Current implementation status:** Existing `task.started` and terminal mission events provide the foundation. A durable per-attempt claim and authoritative outcome reconciliation are not yet implemented. Tests authored in the open PRs remain unexecuted because hosted CI quota is exhausted and no local project runtime is available.


## Phase A implementation update (2026-11-19)

A first read-only recovery assessment is now implemented on the backend feature branch:

- `services/director/orchestration/recovery.py` provides a pure evidence classifier.
- `MissionOrchestrationRuntime.assess_task_recovery(...)` reads the workspace-scoped canonical task and bounded mission event history; it does not mutate state or invoke an adapter.
- The assessment binds terminal evidence to the same task, mission, workspace, attempt idempotency prefix, correlation ID, and assigned engine.
- Missing timestamps/attribution, duplicate starts, orphan terminal events, conflicting canonical status, incomplete result payloads, or possibly truncated event history fail closed to `outcome_unknown`.
- A failed attempt is only `failed_reconsiderable` when a system recovery-reconciler event carries an explicit no-side-effects assertion and evidence reference. Existing timeout/failure events do not satisfy this condition.
- An approved task with no execution lifecycle events is `not_started`; a fresh execution is `not_stale`; a matching durable completion is classified for result reconciliation, never replay.

Regression tests were added for the four classifications, fresh execution, scope mismatch, contradictory events, and read-only runtime behavior. They are **authored but not executed**. The hosted CI quota remains exhausted and no local project runtime is available.

This is Phase A groundwork only. It does not yet provide an API endpoint, durable attempt ledger, authoritative adapter status lookup, or operator recovery decision workflow. No production migration, data write, deployment, merge, feature-flag change, or CI retry was performed.


## Phase B implementation update (2026-11-19)

### Decision: use the existing mission journal as the attempt ledger

A separate `director_execution_attempts` table is not justified by the current architecture. The production KDO database already has the primitives needed for a durable, append-only attempt ledger:

- `director_mission_events` is append-only and has a unique `(mission_id, idempotency_key)` constraint.
- `append_director_mission_event_with_task_sync(jsonb, jsonb)` appends the event and changes the canonical workspace task from `approved` to `executing` in the same database transaction.
- The RPC locks the mission and task rows, validates workspace/mission/task identity and expected task status, and is executable only by `service_role` (production privileges were read back and verified).
- The mission version check and unique idempotency identity serialize competing claims. A different concurrent claim loses the version/status race.

A second ledger table would duplicate the event identity and task state, creating a new cross-table consistency problem without adding stronger dispatch guarantees. The existing event journal is therefore the durable attempt ledger; no new table or migration is needed for Phase B.

### Attempt identity and claim changes

- The `task.started` event now records explicit `attempt_id`, assigned `engine_id`, assigned `capability`, and initiating operator identity. The attempt ID is the caller's unique idempotency prefix; the start event key is `<attempt_id>:started`.
- The state machine rejects start events whose attempt ID is malformed or whose engine/capability differs from the canonical assignment.
- Runtime checks the append-only journal for a previously used start identity before attempting a new claim. A previously used identity cannot be recycled after a task is reconsidered.
- The atomic event/task transition remains the actual concurrency claim boundary; the preliminary lookup is only a reuse guard.
- A replay acknowledgement for `task.started` now raises `task_start_claim_already_committed` instead of returning a snapshot that could allow a second adapter invocation. This closes the same-idempotency concurrent duplicate dispatch path.
- A newly governed execution must use a fresh attempt identity. A repeated request for an already-claimed attempt is rejected and sent through recovery assessment; it is never dispatched again.

### Crash and concurrency behavior

1. First claimant atomically writes the start event and transitions the canonical task to `executing`.
2. A concurrent request with a different attempt identity fails the mission-version or canonical task-status check.
3. A concurrent duplicate with the same identity receives a replay acknowledgement, which the state machine rejects before adapter invocation.
4. A later request that attempts to reuse an old identity is rejected by the scoped journal lookup.
5. A process interruption after claim but before or during adapter invocation leaves the attempt held as `outcome_unknown` unless authoritative evidence later proves its outcome. No automatic replay is introduced.

### Verification status and release boundary

- Production KDO migration history confirms the existing journal and atomic task-sync migrations are present; the production RPC definition and grants were read back. Its current grants are `postgres` and `service_role` only.
- Regression tests were added for attempt/assignment mismatch and replayed start-claim rejection. All tests remain **authored but unexecuted** because hosted CI quota is exhausted and no local project runtime is available.
- No new production schema, data, migration, deployment, merge, or runtime-flag change was made.
- This completes the Phase B ledger/claim design using existing infrastructure. Phase C remains limited to authoritative adapter status/result lookup contracts for adapters that can actually prove outcomes by attempt ID.


## Phase C implementation update (2026-11-19)

### Reconciliation contract added; existing adapters remain unsupported

- Added `ExecutionAttemptReference` carrying the exact attempt, engine, capability, task, mission, workspace, and correlation identity.
- Added `EngineAttemptReconciliation` with only five explicit statuses: `not_started`, `running`, `completed`, `failed_no_side_effects`, and `unknown`.
- Non-unknown outcomes require an authoritative flag and evidence reference; completed outcomes also require a result summary.
- `AdapterRegistry.reconcile_attempt(...)` calls only an adapter's optional `reconcile_attempt` method, applies the adapter timeout, validates exact attempt/engine identity, and converts missing support, exceptions, malformed responses, or mismatched identities to `unknown`.
- `MissionOrchestrationRuntime.reconcile_task_attempt(...)` reads the canonical task, assignment, and exact start event, then performs this lookup without invoking the engine or mutating the database.
- The original attempt ID is also propagated in `MissionTaskContext` so a future engine can persist it as its own idempotency/status key.

**Important finding:** none of the three currently registered adapters (`content_intelligence`, `meta_engine`, `condition_engine`) exposes authoritative attempt-status lookup. Their current methods perform research, read an in-process health snapshot, or calculate health from existing observations; none can prove whether a particular prior mission attempt started, completed, or had no side effects. They therefore correctly remain `unknown` after an interrupted attempt. Generic engine health and fresh re-invocation are not accepted as reconciliation evidence.

Regression tests were authored for unsupported adapters, exact-identity authoritative responses, mismatched responses, and read-only runtime behavior. They are unexecuted. No production migration, data write, deployment, merge, or runtime activation was performed.


## Phase D implementation update (2026-11-19)

- Added the workspace-scoped POST recovery-review command under the mission orchestration task resource. It uses the signed human workspace actor boundary and operator-level task permission.
- The append-only `task.recovery.reviewed` event records operator, decision, reason, a server-computed recovery classification/reason, correlation identity, and idempotency identity. The client cannot supply or forge the classification.
- Only `hold` and `request_reconciliation` are accepted. The event is review metadata only: it does not change canonical task status, approval provenance, assignment, or execution authority.
- `reconsider` is deliberately rejected. No registered adapter can provide authoritative attempt-specific no-side-effect proof, so an operator assertion alone cannot reopen a task or authorize a new attempt.
- `task.reassigned` now rejects `failed` and `blocked` states. Reassignment is limited to `approval_required` and `approved` tasks until a future authoritative recovery transition exists.
- Native adapter exceptions/timeouts no longer emit `task.failed`. The durable task remains `executing`, preserving the uncertain attempt for reconciliation instead of implying a safe failure.
- Regression tests were added for operator-only review, forbidden reconsideration, unchanged task authority, and reassignment rejection. Tests are authored but unexecuted because hosted CI quota is exhausted and no local project runtime is available.
- No production schema/data change, migration, deployment, merge, runtime-flag activation, or CI retry was performed.



## Phase D follow-up — attempt reconciliation is now exposed and journalled (2026-11-20)

- Added the operator-scoped POST `/missions/{mission_id}/orchestration/tasks/{task_id}/reconcile` endpoint. It requires the authenticated operator role, the trusted workspace context, and a task belonging to the requested workspace mission.
- The endpoint performs only the exact-attempt adapter status lookup. It never calls the execution adapter and never replays the task.
- The observed status is appended as `task.reconciliation.observed` by the trusted recovery reconciler identity. The event is bound to the attempt ID, assigned engine, and capability, and carries the authoritative flag plus any evidence reference and bounded completion summary.
- An `unknown` response may carry diagnostic evidence, but it cannot claim authority. Every non-unknown response must be authoritative and evidence-bound. A completed reconciliation must include a bounded summary and completion timestamp.
- Reconciliation observations do not change canonical task status, assignment, approval provenance, or execution authority. This remains true for blocked tasks, which are now eligible for exact-attempt inspection.
- Runtime task-reassignment synchronization now matches the state machine: only `approval_required` and `approved` tasks are eligible. Failed/blocked tasks remain held.
- Regression coverage was added for the API operator boundary, blocked-attempt lookup/journaling, reconciler identity, evidence requirements, and unchanged task status. These tests are authored but unexecuted because hosted CI quota is exhausted and no local project runtime is available.
- No production schema/data change, migration, deployment, merge, runtime-flag activation, or CI retry was performed.
