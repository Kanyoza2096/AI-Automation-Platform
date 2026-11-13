import asyncio
from types import SimpleNamespace

from services.engagement_tracker import EngagementTracker, PostEngagement


class CapturingConditionEngine:
    def __init__(self):
        self.observations = []

    def observe(self, observation):
        self.observations.append(observation)
        return {}


class FakeQuery:
    def __init__(self, table):
        self.table_name = table

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == "brands":
            return SimpleNamespace(data=[{
                "id": "brand-1",
                "name": "Test brand",
                "workspace_id": "workspace-1",
            }])
        if self.table_name == "post_history":
            return SimpleNamespace(data=[])
        raise AssertionError(f"Unexpected table: {self.table_name}")


class FakeSupabase:
    def table(self, name):
        return FakeQuery(name)


def test_workspace_id_is_carried_in_memory_but_not_written_to_engagement_row():
    engagement = PostEngagement(
        id="eng-1",
        post_id="post-1",
        brand_id="brand-1",
        workspace_id="workspace-1",
    )

    assert engagement.workspace_id == "workspace-1"
    assert "workspace_id" not in engagement.model_dump()


def test_evaluate_rules_passes_workspace_id_to_condition_engine_observation():
    async def run():
        engine = CapturingConditionEngine()
        tracker = EngagementTracker(condition_engine=engine)
        engagement = PostEngagement(
            id="eng-1",
            post_id="post-1",
            brand_id="brand-1",
            workspace_id="workspace-1",
            likes=3,
        )

        async def fetch_recent_engagement(hours_back=24):
            return [engagement]

        async def calculate_engagement_ratio(_engagement):
            return 1.0

        tracker.fetch_recent_engagement = fetch_recent_engagement
        tracker.calculate_engagement_ratio = calculate_engagement_ratio

        await tracker.evaluate_rules()

        assert len(engine.observations) == 1
        assert engine.observations[0].metadata["workspace_id"] == "workspace-1"

    asyncio.run(run())


def test_dead_brand_observation_includes_workspace_id():
    async def run():
        engine = CapturingConditionEngine()
        tracker = EngagementTracker(
            supabase_client=FakeSupabase(),
            condition_engine=engine,
        )

        await tracker.check_dead_brands()

        assert len(engine.observations) == 1
        assert engine.observations[0].metadata["workspace_id"] == "workspace-1"

    asyncio.run(run())
