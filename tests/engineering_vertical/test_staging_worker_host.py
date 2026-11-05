import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from services.kdo_engineering_postgres_event_store import PostgresEngineeringEventStore
from services.kdo_engineering_staging_host import (
    StagingWorkerHostConfigurationError,
    _build_authenticated_workflow_authorizer,
    _project_ref_from_settings,
)


class FakeEventCursor:
    def __init__(self, connection):
        self.connection = connection
        self.one = None

    def execute(self, sql, params=None):
        if "SELECT sequence_no,event_hash" in sql:
            if self.connection.events:
                latest = self.connection.events[-1]
                self.one = (latest[4], latest[10])
            else:
                self.one = None
        elif "INSERT INTO public.kdo_engineering_console_events" in sql:
            self.connection.events.append((
                params[0], params[1], params[2], params[3], params[4],
                params[5], params[6], params[7], params[8], params[9], params[10],
            ))
        elif "SELECT event_id,task_id,tenant_id,workspace_id,sequence_no" in sql:
            self.connection.selected = list(self.connection.events)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.connection.selected

    def close(self):
        pass


class FakeEventConnection:
    def __init__(self):
        self.events = []
        self.selected = []

    def cursor(self):
        return FakeEventCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_postgres_event_store_preserves_full_scope_and_hash_chain():
    connection = FakeEventConnection()
    store = PostgresEngineeringEventStore(lambda: connection)

    first = store.append(
        task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        event_type="worker.started", actor_id="worker-1", payload={"safe": True},
    )
    second = store.append(
        task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        event_type="worker.finished", actor_id="worker-1", payload={"status": "ok"},
        expected_previous_hash=first["event_hash"],
    )

    assert first["tenant_id"] == "tenant-1"
    assert first["workspace_id"] == "workspace-1"
    assert first["sequence_no"] == 1
    assert second["sequence_no"] == 2
    assert second["previous_hash"] == first["event_hash"]
    assert store.verify_chain(
        task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1"
    )


def test_postgres_event_store_rejects_wrong_previous_hash():
    connection = FakeEventConnection()
    store = PostgresEngineeringEventStore(lambda: connection)
    store.append(
        task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        event_type="worker.started", actor_id="worker-1", payload={},
    )
    with pytest.raises(Exception, match="event_chain_compare_and_swap_conflict"):
        store.append(
            task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
            event_type="worker.finished", actor_id="worker-1", payload={},
            expected_previous_hash="wrong",
        )


def _worker_config():
    return ProductionWorkerConfig(
        enabled=True,
        database_url="postgresql://postgres.project-ref@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
        worker_id="worker-1", tenant_id="tenant-1",
        workspace_id="workspace-1", actor_id="token-1",
    )


def test_project_guard_requires_exact_staging_supabase_and_pooler_ref():
    settings = SimpleNamespace(
        environment="staging",
        supabase_url="https://project-ref.supabase.co",
    )
    assert _project_ref_from_settings(
        settings, _worker_config(),
        {"KDO_ENGINEERING_EXPECTED_SUPABASE_PROJECT_REF": "project-ref"},
    ) == "project-ref"


def test_project_guard_rejects_production_environment():
    settings = SimpleNamespace(
        environment="production",
        supabase_url="https://project-ref.supabase.co",
    )
    with pytest.raises(StagingWorkerHostConfigurationError, match="worker_environment_must_be_staging"):
        _project_ref_from_settings(
            settings, _worker_config(),
            {"KDO_ENGINEERING_EXPECTED_SUPABASE_PROJECT_REF": "project-ref"},
        )


class FakeQuery:
    def __init__(self, table, records):
        self.table_name = table
        self.records = records
        self.filters = {}

    def select(self, _columns):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def maybe_single(self):
        return self

    def execute(self):
        record = self.records.get(self.table_name)
        if record and all(record.get(key) == value for key, value in self.filters.items()):
            return SimpleNamespace(data=record)
        return SimpleNamespace(data=None)


class FakeDatabase:
    def __init__(self, token):
        token = dict(token)
        token["token_hash"] = hashlib.sha256(b"kny_test_secret").hexdigest()
        self.records = {
            "api_tokens": token,
            "workspaces": {"id": "workspace-1", "status": "active"},
        }

    def table(self, name):
        return FakeQuery(name, self.records)


class FakeScopeCursor:
    def execute(self, _sql, _params):
        pass

    def fetchone(self):
        return (True,)

    def close(self):
        pass


class FakeScopeConnection:
    def cursor(self):
        return FakeScopeCursor()

    def close(self):
        pass


def test_authenticated_workflow_authorizer_checks_actor_scope_role_and_token():
    token = {
        "id": "token-1", "workspace_id": "workspace-1",
        "permissions": ["read", "write"], "expires_at": None, "revoked": False,
    }
    config = _worker_config()
    authorize = _build_authenticated_workflow_authorizer(
        db=FakeDatabase(token), config=config, worker_token="kny_test_secret",
        connection_factory=FakeScopeConnection,
    )
    assert authorize("token-1", "execute_build", "tenant-1", "workspace-1") is True
    assert authorize("other-token", "execute_build", "tenant-1", "workspace-1") is False
    assert authorize("token-1", "execute_build", "other-tenant", "workspace-1") is False
    assert authorize("token-1", "execute_build", "tenant-1", "other-workspace") is False
    assert authorize("token-1", "submit_plan", "tenant-1", "workspace-1") is False


def test_authenticated_workflow_authorizer_rejects_read_only_and_expired_tokens():
    config = _worker_config()
    read_only = {
        "id": "token-1", "workspace_id": "workspace-1",
        "permissions": ["read"], "expires_at": None, "revoked": False,
    }
    authorize = _build_authenticated_workflow_authorizer(
        db=FakeDatabase(read_only), config=config, worker_token="kny_test_secret",
        connection_factory=FakeScopeConnection,
    )
    assert authorize("token-1", "execute_build", "tenant-1", "workspace-1") is False

    expired = {
        "id": "token-1", "workspace_id": "workspace-1",
        "permissions": ["write"],
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "revoked": False,
    }
    authorize_expired = _build_authenticated_workflow_authorizer(
        db=FakeDatabase(expired), config=config, worker_token="kny_test_secret",
        connection_factory=FakeScopeConnection,
    )
    assert authorize_expired("token-1", "execute_build", "tenant-1", "workspace-1") is False
