# Director Dynamic Swarm Foundation

## Current implementation

The Director orchestration runtime now has a bounded, durable agent roster model.

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

This increment provides durable planning, registration, and lifecycle state, not autonomous agent cognition or worker execution. The next increment must connect registered agent records to approved, advisory-only native adapter invocations, persist bounded contributions and usage, and expose live agent progress in Mission Control. The Director's model-driven complexity assessment and autonomous follow-up spawning should be enabled only after that execution path and its tests are complete.

## Validation

Unit tests cover complexity-based sizing, candidate capability/task validation, duplicate detection, resource ceilings, lifecycle authorization, active-agent completion invariants, cancellation, and adding later specialist waves. The tests still require execution in the repository's CI environment before merge.
