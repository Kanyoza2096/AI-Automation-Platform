# Director Dynamic Swarm Foundation

## Current implementation

The Director orchestration runtime now has a bounded, durable agent roster model and a model-driven assessment path.

- The authenticated workspace-scoped endpoint `POST /missions/{mission_id}/orchestration/swarm/assess` asks the existing KDO structured LLM gateway to assess complexity and propose specialist assignments.
- The model receives only selected mission task summaries, bounded prior agent findings, and the allowlisted native adapter/capability catalogue. Task parameters, credentials, executable handles, and external-action tools are not supplied.
- The model cannot invent engine IDs, capabilities, or task IDs. The assessment contract validates every assignment against the registry and mission task scope before the existing deterministic planner sees it.
- The planner still enforces complexity-based target sizing, per-task diversity, active concurrency, total agent count, and estimated token ceilings. Deterministic agent IDs are derived from the caller's idempotency prefix and proposed assignment.
- Prior completed specialist summaries/findings are included in later assessments so Director can recommend a follow-up wave for unresolved gaps, or explicitly decide that no additional specialists are needed. After each specialist contribution is persisted, the agent-execution endpoint automatically triggers this reassessment and registers a justified follow-up wave; duplicate completed/active assignments are rejected.
- If the LLM gateway is unavailable or returns invalid/unsafe output, assessment fails closed and no agents are spawned.
- The response includes a concise rationale and provider request metadata for traceability; it explicitly reports that execution and external-action authority were not started or granted.

- `plan_swarm` derives a target roster size from a normalized complexity score and validates proposed agents against the trusted native adapter registry.
- `MissionOrchestrationRuntime.spawn_swarm` plans and appends one `agent.spawned` event per selected agent. It can be called again during an active mission to add a later specialist wave.
- `MissionStateMachine` persists agent records in the mission state snapshot and validates append-only lifecycle changes.
- `agent.status.changed` supports Director-controlled transitions from spawned to running, completed, failed, or terminated.
- Mission failure/cancellation closes active agent records; mission completion is rejected while agents remain active.
- The authenticated API exposes `POST /missions/{mission_id}/orchestration/swarm/spawn`; the orchestration timeline includes the persisted `agents` map.

## Safety and limits

- The Director is the only actor allowed to spawn agents or change their lifecycle status.
- Every candidate must use an existing mission task and a registered adapter capability.
- No dynamic task creation is allowed in this increment. Agents collaborate on already admitted mission tasks.
- Default mission ceilings: 8 total agent records, 4 concurrently active agents, 3 active agents per task, and 12,000 estimated tokens across the swarm.
- Existing adapter timeouts, concurrency controls, workspace scoping, and mission journal idempotency remain in force.
- The API is operator-authorized and records the trusted human actor as the Director actor.
- Swarm registration does not invoke an engine, approve a canonical task, or grant execution/external-action authority. The API explicitly reports `execution_started: false`.

## Advisory specialist execution

Registered specialists can now be invoked through the existing native adapter registry.

- Runtime entry point: `MissionOrchestrationRuntime.execute_agent`.
- Authenticated workspace-scoped API: `POST /missions/{mission_id}/orchestration/agents/{agent_id}/execute`.
- The Director moves the agent through running, contribution-recorded, and completed/failed lifecycle events.
- Contributions are bounded and persisted in the agent's MissionState record, including findings, recommendations, evidence references, confidence, token usage, and provider-call usage.
- Native adapter identity, capability, mission, task, and workspace are checked by the existing registry/context boundary.
- Actual usage cannot exceed the agent's reserved token estimate or the mission budget.
- This path does not require or change canonical task approval because it is advisory analysis only; it never marks a task completed, verified, or approved.
- The endpoint reports `external_action_authority_granted: false` and `canonical_task_state_changed: false`.

## Deliberate boundary

This increment provides model-driven complexity assessment, bounded roster selection, durable registration, and automatic Director reassessment/spawning after a specialist contribution. Specialist execution remains an authenticated advisory endpoint; newly spawned follow-up agents are not auto-executed. Each follow-up still passes through the same workspace-scoped endpoint and mission budgets. A future increment should add a governed bounded worker loop that executes selected advisory agents and triggers reassessment without exceeding the existing mission caps, plus the dedicated live swarm visualization in Mission Control.

## Validation

Unit tests cover model output validation, workspace-scoped gateway use, no-spawn decisions after completed findings, rejection of invented engine/capability/task assignments, provider-unavailable fail-closed behavior, complexity-based sizing, resource ceilings, lifecycle authorization, cancellation, and later specialist waves. CI must pass on the current PR head before merge.
