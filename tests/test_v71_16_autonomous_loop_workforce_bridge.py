import asyncio
import pytest

from services.v71_16_autonomous_loop_workforce_bridge import AutonomousLoopWorkforceBridge


class FakeRuntime:
    def workspace_runtime(self, workspace_id):
        return {"bounded_autonomy": FakeOps(workspace_id), "durable_runtime": FakeDurable()}

class FakeOps:
    def __init__(self, workspace_id): self.workspace_id = workspace_id
    def create(self, **kwargs):
        class Run:
            run_id = kwargs["run_id"]
            workspace_id = kwargs["workspace_id"] if "workspace_id" in kwargs else "ws"
        return Run()

class FakeSnapshot:
    def __init__(self, run): self.run = run

class FakeDurable:
    async def persist(self, run): return FakeSnapshot(run)


def test_missing_workforce_configuration_is_advisory_only():
    bridge = AutonomousLoopWorkforceBridge(production_runtime=FakeRuntime())
    proposal = asyncio.run(bridge.propose({"workspace_id": "ws"}, capability="publish_content"))
    assert proposal.disposition == "ADVISORY_ONLY"
    assert proposal.execution_requested is False
    assert proposal.authority_granted is False


def test_configured_workforce_produces_durable_candidate_without_execution():
    payload = {"workspace_id": "ws", "agent_id": "agent", "plan_id": "plan", "workflow_id": "wf", "objective": "grow"}
    bridge = AutonomousLoopWorkforceBridge(production_runtime=FakeRuntime())
    proposal = asyncio.run(bridge.propose(payload, capability="notify"))
    assert proposal.disposition == "EXECUTION_CANDIDATE"
    assert proposal.run_id
    assert proposal.execution_requested is False
    assert proposal.authority_granted is False


def test_status_proves_no_parallel_execution_plane():
    bridge = AutonomousLoopWorkforceBridge(production_runtime=FakeRuntime())
    status = bridge.status()
    assert status["parallel_execution_plane"] is False
    assert status["execution_requested_by_bridge"] is False
