from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.kdo_engineering_phase16_5 import (
    Phase165Error, PostgresEngineeringTaskRepository, PostgresEngineeringWorkspaceRepository,
)
from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus
from services.kdo_engineering_task_repository import EngineeringTaskScopeError

ROOT = Path(__file__).resolve().parents[2]


class FakeConnection:
    def __init__(self, cursor): self._cursor = cursor; self.commits = 0; self.rollbacks = 0; self.closed = False
    def cursor(self): return self._cursor
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


class FakeCursor:
    def __init__(self, row=None): self.row = row; self.rows = []; self.sql = []; self.params = []; self.closed = False
    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split())); self.params.append(params)
        if "INSERT INTO public.kdo_engineering_tasks" in sql and "RETURNING *" in sql:
            p = params
            self.row = {"task_id":p[0],"tenant_id":p[1],"workspace_id":p[2],"objective":p[3],
                "acceptance_criteria":json.loads(p[4]),"idempotency_key":p[5],"status":p[6],"revision":p[7],
                "repository_ref":p[8],"base_revision":p[9],"metadata":json.loads(p[10]),"evidence":json.loads(p[11]),
                "fingerprint":p[12]}
        elif "UPDATE public.kdo_engineering_tasks" in sql:
            self.row = {"task_id":params[4]}
        elif "SELECT * FROM public.kdo_engineering_tasks" in sql:
            pass
    def fetchone(self):
        row, self.row = self.row, None
        return row
    def fetchall(self): return self.rows
    def close(self): self.closed = True


def _task():
    return EngineeringTaskContract(task_id="task-1",tenant_id="tenant-1",workspace_id="ws-1",
        objective="Add tests",acceptance_criteria=("tests pass",),idempotency_key="idem-1")


def test_repositories_require_explicit_connection_factory():
    with pytest.raises(Phase165Error): PostgresEngineeringTaskRepository(None)
    with pytest.raises(Phase165Error): PostgresEngineeringWorkspaceRepository(None)


def test_task_create_uses_postgres_jsonb_and_database_idempotency_constraint():
    cursor = FakeCursor(); connections=[]
    def factory():
        c=FakeConnection(cursor); connections.append(c); return c
    saved = PostgresEngineeringTaskRepository(factory).create(_task())
    assert saved.task_id == "task-1"
    assert "ON CONFLICT DO NOTHING RETURNING *" in cursor.sql[0]
    assert "%s::jsonb" in cursor.sql[0]
    assert connections[0].commits == 1 and connections[0].closed


def test_scoped_task_read_rejects_missing_scope_before_database_access():
    def should_not_connect(): raise AssertionError("must not connect")
    repo = PostgresEngineeringTaskRepository(should_not_connect)
    with pytest.raises(EngineeringTaskScopeError): repo.get("task-1",tenant_id="",workspace_id="ws-1")


def test_revision_mutation_uses_row_lock_and_compare_and_swap():
    task = _task()
    row = {"task_id":task.task_id,"tenant_id":task.tenant_id,"workspace_id":task.workspace_id,
        "objective":task.objective,"acceptance_criteria":{"items":["tests pass"]},"idempotency_key":task.idempotency_key,
        "status":task.status.value,"revision":task.revision,"repository_ref":None,"base_revision":None,
        "metadata":{},"evidence":{},"fingerprint":task.fingerprint()}
    cursor = FakeCursor(row=row)
    def factory(): return FakeConnection(cursor)
    repo = PostgresEngineeringTaskRepository(factory)
    updated = repo.update_evidence("task-1",tenant_id="tenant-1",workspace_id="ws-1",expected_revision=1,evidence={"marker":"ok"})
    assert updated.revision == 2 and updated.evidence["marker"] == "ok"
    assert any("FOR UPDATE" in sql for sql in cursor.sql)
    assert any("revision=%s RETURNING task_id" in sql for sql in cursor.sql)


def test_migration_defines_shared_scope_fk_rls_and_server_only_grants():
    sql = (ROOT / "migrations/141_kdo_engineering_shared_metadata.sql").read_text().lower()
    assert "create table if not exists public.kdo_engineering_tasks" in sql
    assert "create table if not exists public.kdo_engineering_workspaces" in sql
    assert "create table if not exists public.kdo_engineering_plan_reviews" in sql
    assert "unique (tenant_id,workspace_id,idempotency_key)" in sql
    assert "foreign key (tenant_id,workspace_id,task_id)" in sql
    assert sql.count("enable row level security") == 3
    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql


def test_metadata_adapters_do_not_silently_fall_back_to_sqlite():
    source = (ROOT / "services/kdo_engineering_phase16_5.py").read_text()
    assert "sqlite3" not in source
    assert "_connection_factory()" in source
    assert "postgres_engineering_metadata_operation_failed" in source

class AtomicReviewCursor:
    def __init__(self, row, member_count=2):
        self.row=row; self.current=None; self.sql=[]; self.params=[]; self.closed=False
        self.member_count=member_count; self.rowcount=1
    def execute(self, sql, params=None):
        normalized=" ".join(sql.split()); self.sql.append(normalized); self.params.append(params)
        if normalized.startswith("SELECT COUNT(*) FROM public.workspace_members"):
            self.current=(self.member_count,)
        elif normalized.startswith("SELECT * FROM public.kdo_engineering_tasks"):
            self.current=self.row
        elif normalized.startswith("UPDATE public.kdo_engineering_tasks"):
            self.current={"task_id":params[4]}
        elif normalized.startswith("INSERT INTO public.kdo_engineering_plan_reviews"):
            self.current={"task_id":params[2]}
        else: self.current=None
    def fetchone(self): row,self.current=self.current,None; return row
    def fetchall(self): return []
    def close(self): self.closed=True


def test_plan_approval_and_ledger_commit_in_one_postgres_transaction():
    from services.kdo_engineering_task_repository import _encode
    from services.kdo_engineering_phase16_2 import _plan_digest
    steps=["Edit app.py", "Run checks"]
    task=EngineeringTaskContract(task_id="task-atomic",tenant_id="tenant-1",workspace_id="ws-1",
        objective="Change app",acceptance_criteria=("checks pass",),idempotency_key="idem-atomic",
        evidence={"plan_review":{"state":"submitted","steps":steps,"digest":_plan_digest(steps),"submitted_by":"author"}})
    data=_encode(task)
    row={**data,"acceptance_criteria":json.loads(data["acceptance_criteria"]),
         "metadata":json.loads(data["metadata"]),"evidence":json.loads(data["evidence"])}
    cursor=AtomicReviewCursor(row); connections=[]
    def factory():
        connection=FakeConnection(cursor); connections.append(connection); return connection
    result=PostgresEngineeringTaskRepository(factory).review_plan_atomically(
        task.task_id,tenant_id=task.tenant_id,workspace_id=task.workspace_id,
        expected_revision=1,reviewer_id="reviewer",approve=True,reason="Approved")
    assert result.status == EngineeringTaskStatus.PLANNED
    assert result.evidence["plan_review"]["approved_digest"] == _plan_digest(steps)
    assert any("FOR UPDATE" in sql for sql in cursor.sql)
    assert any(sql.startswith("UPDATE public.kdo_engineering_tasks") for sql in cursor.sql)
    assert any(sql.startswith("INSERT INTO public.kdo_engineering_plan_reviews") for sql in cursor.sql)
    assert connections[0].commits == 1 and connections[0].rollbacks == 0


def test_atomic_review_allows_submitter_to_approve_plan():
    from services.kdo_engineering_task_repository import _encode
    from services.kdo_engineering_phase16_2 import _plan_digest
    steps=["Edit app.py"]
    task=EngineeringTaskContract(task_id="task-self",tenant_id="tenant-1",workspace_id="ws-1",
        objective="Change app",acceptance_criteria=("checks pass",),idempotency_key="idem-self",
        evidence={"plan_review":{"state":"submitted","steps":steps,"digest":_plan_digest(steps),"submitted_by":"same-person"}})
    data=_encode(task)
    row={**data,"acceptance_criteria":json.loads(data["acceptance_criteria"]),
         "metadata":json.loads(data["metadata"]),"evidence":json.loads(data["evidence"])}
    cursor=AtomicReviewCursor(row); connection=FakeConnection(cursor)
    approved=PostgresEngineeringTaskRepository(lambda: connection).review_plan_atomically(
        task.task_id,tenant_id=task.tenant_id,workspace_id=task.workspace_id,
        expected_revision=1,reviewer_id="same-person",approve=True)
    assert approved.status == EngineeringTaskStatus.PLANNED
    assert approved.evidence["plan_review"]["reviewed_by"] == "same-person"
    assert any(sql.startswith("UPDATE public.kdo_engineering_tasks") for sql in cursor.sql)
    assert any(sql.startswith("INSERT INTO public.kdo_engineering_plan_reviews") for sql in cursor.sql)
    assert connection.commits == 1 and connection.rollbacks == 0


def test_atomic_review_submitter_approval_does_not_depend_on_member_count():
    from services.kdo_engineering_task_repository import _encode
    from services.kdo_engineering_phase16_2 import _plan_digest
    steps=["Edit app.py"]
    task=EngineeringTaskContract(task_id="task-single",tenant_id="tenant-1",workspace_id="ws-single",
        objective="Change app",acceptance_criteria=("checks pass",),idempotency_key="idem-single",
        evidence={"plan_review":{"state":"submitted","steps":steps,"digest":_plan_digest(steps),"submitted_by":"same-person"}})
    data=_encode(task)
    row={**data,"acceptance_criteria":json.loads(data["acceptance_criteria"]),
         "metadata":json.loads(data["metadata"]),"evidence":json.loads(data["evidence"])}
    cursor=AtomicReviewCursor(row,member_count=1); connection=FakeConnection(cursor)
    approved=PostgresEngineeringTaskRepository(lambda: connection).review_plan_atomically(
        task.task_id,tenant_id=task.tenant_id,workspace_id=task.workspace_id,
        expected_revision=1,reviewer_id="same-person",approve=True,reason="Single operator approval")
    assert approved.status == EngineeringTaskStatus.PLANNED
    assert approved.evidence["plan_review"]["reviewed_by"] == "same-person"
    assert not any("workspace_members" in sql for sql in cursor.sql)
    assert connection.commits == 1 and connection.rollbacks == 0


def test_postgres_workflow_composition_injects_postgres_adapters_explicitly():
    from services.kdo_engineering_phase16_6 import create_postgres_engineering_workflow
    workflow=create_postgres_engineering_workflow(connection_factory=lambda: None,
        source_repository_path="/tmp/source",managed_root="/tmp/managed",authorize=lambda *_: True)
    assert isinstance(workflow.tasks, PostgresEngineeringTaskRepository)
    assert isinstance(workflow.workspaces, PostgresEngineeringWorkspaceRepository)
