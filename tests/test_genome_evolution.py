from organism.dreaming import DreamEngine
from organism.genome import GenomeEngine, StrategyGenome
from organism.memory import MemoryStore
from organism.world_model import WorldModel


def test_evolution_generates_bounded_variants_from_action_pool():
    engine = GenomeEngine(DreamEngine(MemoryStore(), WorldModel()))
    seed = StrategyGenome("w1", "base", ("publish",))
    result = engine.evolve("w1", seed, action_pool=("publish", "notify", "review"))
    assert len(result.candidates) > 1
    assert all(candidate.workspace_id == "w1" for candidate in result.candidates)
    assert result.best is not None


def test_evolution_does_not_require_hashable_strategy_parameters():
    engine = GenomeEngine(DreamEngine(MemoryStore(), WorldModel()))
    seed = StrategyGenome("w1", "base", ("publish",), parameters={"channels": ["facebook"]})
    result = engine.evolve("w1", seed, action_pool=("publish", "notify"))
    assert result.best is not None
