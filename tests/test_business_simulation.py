from organism.dreaming import DreamEngine
from organism.memory import MemoryStore
from organism.world_model import WorldModel
from services.business_simulation import BusinessSimulationProjector
from models.business_simulation import Scenario, ScenarioAction, ScenarioDisposition


def _scenario(ws="w1", sid="s1", action="publish"):
    return Scenario(sid, ws, sid, objective="test", actions=(ScenarioAction(action),))


def test_projects_existing_dream_without_execution_authority():
    memory, world = MemoryStore(), WorldModel()
    dream = DreamEngine(memory, world).simulate("w1", ["publish"])
    result = BusinessSimulationProjector().project(workspace_id="w1", scenarios=[_scenario()], dreams={"s1": dream})
    item = result.scenarios[0]
    assert item.production_mutated is False
    assert item.authority_granted is False
    assert item.execution_started is False
    assert result.best_scenario_id is None or result.best_scenario_id == "s1"


def test_workspace_isolation():
    result = BusinessSimulationProjector().project(workspace_id="w1", scenarios=[_scenario("w2")])
    assert result.scenarios == ()
    assert "scenario_workspace_mismatch:s1" in result.warnings


def test_missing_simulation_is_inconclusive_not_guessed():
    result = BusinessSimulationProjector().project(workspace_id="w1", scenarios=[_scenario()])
    assert result.scenarios[0].disposition is ScenarioDisposition.INCONCLUSIVE
    assert result.best_scenario_id is None


def test_blocked_scenario_cannot_win_comparison():
    memory, world = MemoryStore(), WorldModel()
    dream = DreamEngine(memory, world).simulate("w1", ["publish"])
    result = BusinessSimulationProjector().project(
        workspace_id="w1",
        scenarios=[_scenario(sid="blocked"), _scenario(sid="open")],
        dreams={"blocked": dream, "open": dream},
        blocked_scenario_ids=["blocked"],
    )
    assert result.scenarios[0].disposition is ScenarioDisposition.BLOCKED
    assert result.best_scenario_id != "blocked"


def test_fingerprint_and_order_are_deterministic():
    memory, world = MemoryStore(), WorldModel()
    dream = DreamEngine(memory, world).simulate("w1", ["publish"])
    p = BusinessSimulationProjector()
    a = p.project(workspace_id="w1", scenarios=[_scenario(sid="b"), _scenario(sid="a")], dreams={"a": dream, "b": dream})
    b = p.project(workspace_id="w1", scenarios=[_scenario(sid="a"), _scenario(sid="b")], dreams={"a": dream, "b": dream})
    assert a.to_dict() == b.to_dict()
    assert all(x.fingerprint for x in a.scenarios)
