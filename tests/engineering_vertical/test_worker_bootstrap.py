import threading
from types import SimpleNamespace

import pytest

from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue
from services.kdo_engineering_worker_bootstrap import (
    EngineeringWorkerBootstrapError,
    ProductionWorkerConfig,
    run_production_engineering_worker,
    validate_production_worker_config,
)


def config(**overrides):
    values = dict(
        enabled=True,
        database_url="postgresql://worker:secret@db.example.test:5432/kdo",
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
    )
    values.update(overrides)
    return ProductionWorkerConfig(**values)


class Cursor:
    def __init__(self, row=( "kdo_engineering_build_jobs", "kdo_engineering_tasks", "kdo_engineering_workspaces")):
        self.row = row
        self.sql = None

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return self.row

    def close(self):
        pass


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_production_worker_requires_explicit_opt_in():
    with pytest.raises(EngineeringWorkerBootstrapError, match="not_explicitly_enabled"):
        validate_production_worker_config(config(enabled=False))


@pytest.mark.parametrize("database_url", [
    "sqlite:///tmp/kdo.db",
    "https://db.example.test/kdo",
    "postgresql://",
])
def test_production_worker_rejects_non_postgres_or_incomplete_database_url(database_url):
    with pytest.raises(EngineeringWorkerBootstrapError, match="postgres_database_url_required"):
        validate_production_worker_config(config(database_url=database_url))


def test_production_worker_rejects_invalid_identity_and_lease():
    with pytest.raises(EngineeringWorkerBootstrapError, match="identity_and_scope"):
        validate_production_worker_config(config(worker_id=" "))
    with pytest.raises(EngineeringWorkerBootstrapError, match="lease_seconds"):
        validate_production_worker_config(config(lease_seconds=10))


def test_bootstrap_requires_postgres_queue_and_never_falls_back_to_sqlite():
    with pytest.raises(EngineeringWorkerBootstrapError, match="postgres_queue_required"):
        run_production_engineering_worker(
            config=config(),
            connection_factory=lambda: None,
            queue=object(),
            workflow=object(),
            executor=lambda **_: {},
            stopping=threading.Event(),
        )


def test_bootstrap_checks_schema_before_worker_start():
    cursor = Cursor(row=(None, "kdo_engineering_tasks", "kdo_engineering_workspaces"))
    connection = Connection(cursor)
    with pytest.raises(EngineeringWorkerBootstrapError, match="schema_not_ready"):
        run_production_engineering_worker(
            config=config(),
            connection_factory=lambda: connection,
            queue=PostgresEngineeringBuildQueue(lambda: None),
            workflow=object(),
            executor=lambda **_: {},
            stopping=threading.Event(),
        )
    assert connection.closed


def test_bootstrap_runs_scoped_worker_and_publishes_lifecycle_health(monkeypatch):
    import services.kdo_engineering_worker_bootstrap as bootstrap

    cursor = Cursor()
    connection = Connection(cursor)
    queue = PostgresEngineeringBuildQueue(lambda: None)
    captured = {}
    health_events = []

    def fake_worker(**kwargs):
        captured.update(kwargs)
        kwargs["on_result"](SimpleNamespace(job_id="job-42"), {"status": "awaiting_review"})
        return {"claimed": 1, "completed": 1, "execution_errors": 0,
                "claim_errors": 0, "stopped": True}

    monkeypatch.setattr(bootstrap, "run_engineering_build_worker", fake_worker)
    result = run_production_engineering_worker(
        config=config(),
        connection_factory=lambda: connection,
        queue=queue,
        workflow=object(),
        executor=lambda **_: {},
        stopping=threading.Event(),
        health_sink=lambda event: health_events.append(dict(event)),
        max_jobs=1,
    )

    assert captured["tenant_id"] == "tenant-a"
    assert captured["workspace_id"] == "workspace-a"
    assert captured["worker_id"] == "worker-a"
    assert captured["queue"] is queue
    assert captured["max_jobs"] == 1
    assert result["health"]["status"] == "stopped"
    assert result["health"]["last_job_id"] == "job-42"
    assert [event["status"] for event in health_events] == ["running", "stopped"]
    assert connection.closed
