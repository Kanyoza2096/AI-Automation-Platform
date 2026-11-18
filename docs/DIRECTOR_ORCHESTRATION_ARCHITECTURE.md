# KDO Director Orchestration Architecture

**Status:** Architecture baseline / audit-backed proposal  
**Target:** Backend `staging`  
**Compatibility rule:** Additive and backward-compatible. Existing KDO engines, APIs, jobs, governance, and standalone workflows remain operational.

## 1. Executive decision

KDO already has meaningful Director foundations: strategic planning, validated task dependency graphs, durable task records, idempotent mission admission, restart reconciliation, capability readiness, a governed execution boundary, and a bounded multi-engine consultation workflow.

The missing capability is not another chat surface. It is a **general-purpose, durable orchestration runtime** that delegates actual work to existing specialist engine runtimes, tracks task-level progress and dependencies, supports durable agent-to-agent communication, and recovers correctly after interruption.

Build this as a new orchestration layer inside KDO AI OS. Do not replace Director, the existing engines, the job service, the AI gateway, or the Action Kernel.

## 2. Audit findings (source and live-state review)

### Existing reusable backend

- `services/director/core.py`: Director lifecycle, observation, capability catalog, strategic planning, mission staging, dependency validation, mission graph, task scheduler, bounded concurrent execution, approval checks, outcome verification, and recovery assessment.
- `services/director/stores.py`: workspace-scoped task persistence and atomic/idempotent mission admission through `stage_director_mission`.
- `services/director/planning.py`: bounded strategic-plan contract, evidence references, capability validation, DAG validation, advisory-only authority.
- `services/director_bindings.py`, `core/engine_topology.py`, `core/service_definitions.py`: current service bindings and topology/discovery foundations.
- `ai/gateway.py`: workspace-keyed daily token budget with Redis-backed storage when available and an in-process fallback; provider/circuit handling.
- `services/ai_policy.py`: centrally owned task-to-provider routing and ordered fallbacks.
- `services/kdo_engineering_agent_team.py`, `services/kdo_engineering_dag_coordinator.py`, `services/kdo_engineering_persisted_workflow.py`: real engineering-agent assignments, dependency ordering, persisted task lifecycle, and interrupted-task recovery. This is a vertical-specific implementation to integrate, not duplicate as the universal orchestrator.
- Existing governance and action boundaries remain authoritative for approvals and consequential operations.

### Existing collaboration is real but limited

The current `api/routers/ai_chat.py` collaboration path:
1. loads workspace-scoped read-only evidence for selected engines;
2. invokes an LLM consultation for each selected engine using the shared `chat` gateway capability;
3. runs one bounded, coordinator-mediated follow-up question/answer round;
4. compares evidence and disagreements;
5. generates a Director recommendation and synthesis;
6. persists progress in `director_collaboration_runs`.

This is useful advisory collaboration, but it is not yet general-purpose task delegation to each engine's native runtime. Consultations are performed sequentially, and follow-up exchanges are embedded in each consultation JSON record rather than represented as a first-class mission message/event stream. The current selector is bounded to four engines.

Production currently contains two completed collaboration records; both selected only the Content Engine. This is not evidence of multi-engine execution.

### Existing mission/task foundation

- Strategic plans are advisory and validated before admission.
- Mission staging creates workspace-owned tasks in approval-required state through an idempotent database RPC.
- Tasks carry mission IDs and dependencies; the mission graph is currently reconstructed from in-memory task state after startup restoration.
- Tasks found in EXECUTING state at restart are flagged for reconciliation rather than automatically retried.
- The existing scheduler executes eligible dependency-ready tasks concurrently under its semaphore and existing governance checks.

This should remain the canonical task execution foundation wherever its contracts fit.

### Environment parity blocker

Live read-only schema and migration-history inspection found:

- Production has `director_collaboration_runs`, `director_mission_admissions`, and `director_engineering_events`.
- Staging initially lacked those three tables and its Director migration chain diverged from production. A forward-only migration 175 has now added `director_mission_admissions` and a workspace-scope-correct, `SECURITY INVOKER` `stage_director_mission` RPC to staging. The collaboration and engineering-event tables remain absent there.
- The staging migration was verified with RLS/grant checks and a transactionally rolled-back initial-admission/idempotent-replay test. Existing task and plan row counts were unchanged.
- Production has the Director tables and migrations recorded, but its current `stage_director_mission` function omits `scope_type='workspace'`, which conflicts with the live workspace-scope check constraint. A separate production repair is required; production was not changed.
- Production tables have RLS enabled and direct access revoked from `anon` and `authenticated`; service-role access is used by the backend.

Do not replay production migrations wholesale into staging or copy production data. Keep the production RPC repair behind the normal reviewed release gate.

## 3. Target architecture

### 3.1 Director Orchestration Runtime

A deterministic state machine owns:
- mission admission and lifecycle;
- task decomposition and assignment;
- engine selection based on declared, verified capabilities;
- dependency scheduling and bounded parallelism;
- retries/reassignment only when policy and evidence allow;
- deadlines, cancellation, pause/resume, and escalation;
- evidence-based synthesis and decision records.

LLMs propose plans, perform specialist reasoning, and summarize evidence. They do not own orchestration state or grant themselves authority.

### 3.2 Specialist Engine Adapter Registry

Define a typed adapter contract around existing engines. Each adapter declares:
- stable engine and capability IDs;
- native invocation method and input/output schema;
- supported task classes and evidence sources;
- read/write classification and authority requirements;
- timeout, concurrency, and resource limits;
- readiness and runtime dependency evidence;
- cancellation/recovery semantics where supported.

Adapters must call the actual existing engine runtime. A prompt labelled with an engine name is not an acceptable substitute for native engine execution. If an engine has no safe native invocation contract yet, expose it explicitly as advisory-only until one is implemented.

Discover adapters from existing service/topology registries and KCPG command contracts; do not create a second conflicting engine registry.

### 3.3 Persistent mission state and event journal

Reuse:
- `director_tasks` as the canonical persisted task records where its current schema is sufficient;
- `director_mission_admissions` for idempotent admission receipts;
- existing decision, evidence, execution-ledger, and event infrastructure where contracts match;
- `director_collaboration_runs` as a backward-compatible record of legacy advisory consultations.

Add only the missing durable mission aggregate and append-only mission event/message journal after schema parity and contract review. The mission aggregate should capture status, owner/workspace, plan reference, deadlines, budget envelope, current phase, and version. The journal should record assignment, start/progress, question, answer, challenge, evidence reference, disagreement, handoff, reassignment, retry decision, approval transition, completion, failure, and recovery events.

Every state transition must be persisted before it is presented as authoritative. Mission state must be reconstructable after process restart without replaying completed work. Use idempotency keys and optimistic/version fencing to prevent duplicate claims and lost updates.

### 3.4 Task DAG and scheduler

Extend the existing Director dependency model rather than creating a parallel scheduler:
- task owner/engine, capability, objective, input references, expected outputs, acceptance criteria, and dependency IDs;
- bounded parallel execution for independent tasks;
- strict dependency readiness and cycle rejection;
- per-engine concurrency limits and timeout policies;
- lease/claim fencing for multi-worker execution;
- failure classification separating provider/transport faults from analytical failures;
- no blind retry after ambiguous external outcomes.

The engineering DAG coordinator remains the specialized adapter/workflow for software-engineering tasks.

### 3.5 Agent-to-agent communication

Implement durable, typed messages linked to mission, task, sender engine, recipient engine, and correlation IDs. Support:
- clarification requests;
- evidence challenges;
- counterarguments;
- response and rebuttal;
- handoff requests and acknowledgements;
- escalation to Director or operator.

Messages are bounded by round, token, time, and workspace budget limits. A configurable default maximum of three reasoning rounds is a starting policy, not an invitation to loop. Unresolved disagreements remain explicit and are escalated; the system must never manufacture consensus.

### 3.6 Evidence and decision synthesis

Each engine contribution must distinguish:
- observed evidence and stable references;
- interpretation or hypothesis;
- confidence/limitations;
- recommendations;
- unresolved questions.

The Director compares claims against evidence references, detects agreement and disagreement, preserves provenance, and emits a consolidated recommendation with supporting and dissenting evidence. Missing or stale sources remain unknown, not negative findings.

### 3.7 Resource controller

Propagate `workspace_id` through every task, message, provider call, and persistence operation. Reuse the existing AI gateway budget and AI policy; do not hardcode provider/model choices in the orchestrator.

Add mission-level ceilings for tokens, elapsed time, provider calls, concurrent workers, and follow-up rounds. Stop or pause safely when a limit is reached and report the reason. Preserve provider/model provenance and actual usage where available.

### 3.8 Governance boundary

Keep these transitions separate:
1. Director recommendation;
2. operator review and strategic-plan creation;
3. fresh plan/evidence verification;
4. explicit mission admission;
5. task approval under existing policy;
6. governed execution through existing capability/action boundaries;
7. authoritative outcome verification.

Collaboration is advisory and grants no execution authority. Publishing, financial operations, destructive database changes, credential changes, and other consequential actions must continue through existing authorization and Action Kernel controls. No silent authority escalation through engine collaboration.

## 4. Mission Control acceptance criteria

The upgraded Director Mission Control must show:

1. visual collaboration execution timeline;
2. live consultation and task progress for every participating engine;
3. durable engine-to-engine follow-up questions and responses;
4. evidence comparison, provenance, and disagreement detection;
5. Director's consolidated recommendation with unresolved dissent;
6. separate governed transition from recommendation to strategic plan and mission admission.

It must additionally expose task ownership, dependencies, parallel execution, retries/reassignments, handoffs, budget consumption, failures, recovery state, and stale/unavailable sources. The existing collaboration history remains readable during rollout.

## 5. Delivery sequence

### Phase 0 — parity and contracts
- Reconcile staging migrations from the repository and verify staging schema/RLS/grants.
- Trace the server-side KCPG command descriptor/dispatch path end-to-end.
- Produce an engine capability/readiness inventory from actual runtime bindings.
- Identify safe native invocation contracts for the first 2–3 engines.
- Define mission/event schemas, state transitions, idempotency, and rollout flags.

### Phase 1 — durable orchestration kernel
- Implement mission aggregate, append-only event journal, state machine, and persistence adapters.
- Implement adapter registry and first native engine adapters.
- Integrate existing task store, scheduler, gateway budgets, and governance.
- Add restart, duplicate delivery, concurrency, tenant-isolation, budget, and fault-injection tests.
- Keep production feature flag off.

### Phase 2 — genuine multi-engine execution
- Delegate actual bounded tasks to selected specialist engines.
- Add durable messaging, follow-up, evidence challenges, handoffs, and disagreement resolution.
- Prove one multi-engine mission end-to-end in isolated staging with at least three native engine adapters.
- Demonstrate restart recovery without duplicate execution.

### Phase 3 — Mission Control and progressive rollout
- Upgrade the existing Director collaboration surface into the mission timeline and live orchestration view.
- Add operator review, recommendation-to-plan, and plan-to-mission admission visibility.
- Expand adapter coverage progressively; preserve standalone engine APIs and workflows.
- Canary, monitor, and roll back through feature flags if invariants fail.

## 6. Non-negotiable verification gates

- Existing API and engine regression suites remain green.
- No existing task, mission, job, collaboration record, or evidence history is lost.
- Cross-workspace reads/writes are rejected and tested.
- Mission/task/message writes are idempotent and restart-safe.
- Dependency cycles and unresolved prerequisites are blocked.
- Budget, round, concurrency, and timeout limits are enforced deterministically.
- Engine messages cannot invoke tools or grant permissions.
- Advisory collaboration cannot trigger external side effects.
- Every consequential action retains existing approval and receipt requirements.
- Staging end-to-end tests prove actual native engine invocation, durable progress, recovery, and verified outcomes before production rollout.

## 7. Explicit exclusions

- No replacement of the existing Director or specialist engines.
- No wholesale migration to an external orchestration framework without a demonstrated need.
- No second generic job scheduler or duplicate capability registry.
- No production schema mutation during the audit/design phase.
- No claim of multi-agent execution based solely on multiple LLM prompts or a collaboration UI.
