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
