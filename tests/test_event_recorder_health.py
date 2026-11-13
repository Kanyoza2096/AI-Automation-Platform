import asyncio

from domain.analytics.event_recorder import EventRecorder


class FakeBus:
    async def subscribe(self, _event_name, _handler):
        return lambda: None


class FakeRepository:
    def __init__(self, storage_readable=True):
        self.storage_readable = storage_readable

    def check_storage(self):
        return self.storage_readable


def test_event_recorder_reports_degraded_before_subscriptions_start():
    recorder = EventRecorder(FakeRepository(), FakeBus())

    report = recorder.health_check()

    assert report["status"] == "degraded"
    assert report["metrics"]["started"] is False
    assert "event_subscriptions_incomplete" in {
        issue["type"] for issue in report["issues"]
    }


def test_event_recorder_reports_healthy_only_with_subscriptions_and_readable_storage():
    recorder = EventRecorder(FakeRepository(storage_readable=True), FakeBus())

    asyncio.run(recorder.start())
    report = recorder.health_check()
    asyncio.run(recorder.stop())

    assert report["status"] == "healthy"
    assert report["metrics"]["active_subscriptions"] == 4
    assert report["metrics"]["storage_readable"] is True


def test_event_recorder_reports_degraded_when_analytics_storage_is_unavailable():
    recorder = EventRecorder(FakeRepository(storage_readable=False), FakeBus())

    asyncio.run(recorder.start())
    report = recorder.check_health()
    asyncio.run(recorder.stop())

    assert report["status"] == "degraded"
    assert report["metrics"]["storage_readable"] is False
    assert "analytics_storage_unavailable" in {
        issue["type"] for issue in report["issues"]
    }


class FakeQuery:
    def __init__(self, client):
        self.client = client

    def select(self, columns):
        self.client.selected_columns = columns
        return self

    def limit(self, count):
        self.client.limit_count = count
        return self

    def insert(self, row):
        self.client.inserted_row = row
        return self

    def execute(self):
        return type("Response", (), {"data": []})()


class FakeSupabase:
    def table(self, name):
        assert name == "analytics_events"
        return FakeQuery(self)


def test_analytics_repository_health_probe_checks_persisted_schema_contract():
    from repositories.analytics_repository import AnalyticsRepository

    repository = AnalyticsRepository()
    client = FakeSupabase()
    repository._client = client

    assert repository.check_storage() is True
    assert "metadata" in client.selected_columns
    assert "recorded_at" in client.selected_columns
    assert client.limit_count == 1


def test_analytics_repository_preserves_non_column_event_fields_in_metadata():
    from repositories.analytics_repository import AnalyticsRepository

    repository = AnalyticsRepository()
    client = FakeSupabase()
    repository._client = client

    repository.save({
        "event": "post_generated",
        "topic": "security",
        "theme": "engineering",
        "provider": "test-provider",
        "latency_ms": 42,
    })

    assert client.inserted_row["event"] == "post_generated"
    assert client.inserted_row["topic"] == "security"
    assert client.inserted_row["metadata"] == {
        "theme": "engineering",
        "provider": "test-provider",
        "latency_ms": 42,
    }
