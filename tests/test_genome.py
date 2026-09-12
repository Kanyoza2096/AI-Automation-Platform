from organism.dreaming import DreamEngine
from organism.genome import GenomeEngine, StrategyGenome
from organism.goals import Goal
from organism.memory import MemoryStore, Experience, Outcome
from organism.world_model import WorldModel


def _engine():
    memory = MemoryStore()
    world = WorldModel()
    for i, outcome in enumerate([Outcome.SUCCESS, Outcome.SUCCESS, Outcome.FAILURE]):
        memory.record_experience(Experience("w1", None, "publish", outcome))
    return GenomeEngine(DreamEngine(memory, world))


def test_genome_is_declarative_and_has_lineage():
    genome = StrategyGenome("w1", "base", ("publish",), {"retry_limit": 2})
    child = genome.variant(actions=("publish", "verify"))
    assert child.parent_id == genome.genome_id
    assert child.lineage_id == genome.lineage_id
    assert child.generation == 1
    assert len(genome.fingerprint()) == 64


def test_genome_rejects_callable_configuration():
    try:
        StrategyGenome("w1", "bad", ("publish",), {"hook": lambda: None})
        assert False, "expected declarative-only rejection"
    except ValueError as exc:
        assert "declarative" in str(exc)


def test_mutation_is_bounded():
    engine = _engine()
    seed = StrategyGenome("w1", "base", ("publish",))
    variants = engine.mutate(seed, action_pool=["publish", "verify", "notify"])
    assert len(variants) <= engine.MAX_MUTATIONS
    assert all(v.parent_id == seed.genome_id for v in variants)
    assert all(v.workspace_id == "w1" for v in variants)


def test_evolution_uses_dreams_and_never_executes():
    engine = _engine()
    seed = StrategyGenome("w1", "base", ("publish",))
    child = seed.variant(actions=("publish", "publish"))
    result = engine.evolve("w1", seed, candidates=[child], goal=Goal("w1", "ship", "published"))
    assert result.best is not None
    assert all(f.dream.production_mutated is False for f in result.fitness)
    assert all(f.workspace_id == "w1" for f in result.fitness)


def test_evolution_rejects_cross_tenant_candidates():
    engine = _engine()
    seed = StrategyGenome("w1", "base", ("publish",))
    other = StrategyGenome("w2", "other", ("publish",))
    try:
        engine.evolve("w1", seed, candidates=[other])
        assert False, "expected tenant isolation"
    except ValueError as exc:
        assert "workspace_id" in str(exc)
