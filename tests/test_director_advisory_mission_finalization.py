import copy

import pytest

from services.director.orchestration.runtime import MissionOrchestrationRuntime
from services.director.orchestration.state_machine import (
    MissionStateMachine,
    MissionTransitionRejected,
)


class PlatformMissionStore:
    def __init__(self, *, agent_status="completed"):
        self.row = {
            "mission_id": "platform-recovery-mission",
            "scope_type": "platform",
            "workspace_id": None,
            "version": 0,
            "status": "running",
            "phase": "collaboration",
            "task_ids": ["recovery-task"],
            "state_snapshot": {
                "task_ids": ["recovery-task"],
                "task_status": {"recovery-task": "approval_required"},
                "task_assignments": {},
                "engine_progress": {},
                "task_results": {},
                "task_reconciliations": {},
                "evidence_refs": [],
                "unresolved_disagreements": [],
                "pending_handoffs": {},
                "pending_questions": {},
                "agents": {
                    "diagnostician": {
                        "agent_id": "diagnostician",
                        "task_id": "recovery-task",
                        "status": agent_status,
                    }
                },
                "active_reasoning_round": 0,
                "budget": {
                    "max_rounds": 3,
                    "rounds_used": 0,
                    "tokens_used": 0,
                    "provider_calls": 0,
                    "max_tokens": None,
                    "max_provider_calls": None,
                    "exhausted": False,
                },
            },
        }
        self.events = []

    async def get_mission(self, *, mission_id, scope_type, workspace_id=None):
        if mission_id != self.row["mission_id"] or scope_type != "platform" or workspace_id is not None:
            return None
        return copy.deepcopy(self.row)

    async def append_event(self, event):
        if event.expected_version != self.row["version"]:
            return {"error": "director_mission_version_conflict"}
        self.row.update({
            "version": self.row["version"] + 1,
            "status": event.status,
            "phase": event.phase,
            "state_snapshot": copy.deepcopy(dict(event.state_snapshot)),
        })
        self.events.append(event)
        return {"version": self.row["version"], "replayed": False}


@pytest.mark.asyncio
async def test_advisory_recovery_finalizes_partial_and_keeps_tasks_approval_required():
    store = PlatformMissionStore()
    runtime = MissionOrchestrationRuntime(store=store, adapters=object())

    result = await runtime.finalize_advisory_mission(
        mission_id=store.row["mission_id"],
        workspace_id=None,
        idempotency_key="advisory-finalized-once",
        correlation_id="recovery-correlation",
    )

    assert result.status == "partial"
    assert result.phase == "review"
    assert result.state_snapshot["task_status"]["recovery-task"] == "approval_required"
    assert store.events[0].event_type == "mission.partial"
    assert "separate governed human review" in store.events[0].payload["reason"]


@pytest.mark.asyncio
async def test_advisory_recovery_does_not_finalize_while_specialist_is_active():
    store = PlatformMissionStore(agent_status="running")
    runtime = MissionOrchestrationRuntime(store=store, adapters=object())

    with pytest.raises(MissionTransitionRejected, match="advisory_mission_has_active_agents"):
        await runtime.finalize_advisory_mission(
            mission_id=store.row["mission_id"],
            workspace_id=None,
            idempotency_key="advisory-finalized-once",
            correlation_id="recovery-correlation",
        )

    assert store.row["status"] == "running"
    assert store.events == []
