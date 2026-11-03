"""Real PostgreSQL integration coverage for shared task/workspace repositories."""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.getenv("KDO_ENGINEERING_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PostgreSQL integration database")
psycopg = pytest.importorskip("psycopg")

from services.kdo_engineering_postgres_repositories import (
    PostgresEngineeringTaskRepository, PostgresEngineeringWorkspaceRepository,
)
from services.kdo_engineering_task_repository import EngineeringTaskConflict, EngineeringTaskNotFound
from services.kdo_engineering_vertical_contracts import EngineeringTaskContract, EngineeringTaskStatus


@pytest.fixture
def repositories():
    suffix = uuid.uuid4().hex
    tenant, workspace = f"it-tenant-{suffix}", f"it-workspace-{suffix}"
    def connect():
        return psycopg.connect(DSN)
    tasks = PostgresEngineeringTaskRepository(connect)
    workspaces = PostgresEngineeringWorkspaceRepository(connect)
    yield tasks, workspaces, tenant, workspace
    with connect() as conn:
        conn.execute("DELETE FROM public.kdo_engineering_plan_reviews WHERE tenant_id=%s AND workspace_id=%s", (tenant, workspace))
        conn.execute("DELETE FROM public.kdo_engineering_workspaces WHERE tenant_id=%s AND workspace_id=%s", (tenant, workspace))
        conn.execute("DELETE FROM public.kdo_engineering_tasks WHERE tenant_id=%s AND workspace_id=%s", (tenant, workspace))


def make_task(tenant: str, workspace: str, task_id: str = "task-a") -> EngineeringTaskContract:
    return EngineeringTaskContract(
        task_id=task_id, tenant_id=tenant, workspace_id=workspace,
        objective="integration task", acceptance_criteria=("tests pass",),
        idempotency_key="idem-" + task_id,
        metadata={"source": "integration"}, evidence={"initial": True},
    )


def test_postgres_task_repository_roundtrip_idempotency_scope_and_revision_cas(repositories):
    tasks, _workspaces, tenant, workspace = repositories
    task = make_task(tenant, workspace)
    assert tasks.create(task) == task
    assert tasks.create(task) == task
    assert tasks.get(task.task_id, tenant_id=tenant, workspace_id=workspace) == task
    assert tasks.list_scope(tenant_id=tenant, workspace_id=workspace) == [task]

    planned = tasks.transition(task.task_id, EngineeringTaskStatus.PLANNED,
        tenant_id=tenant, workspace_id=workspace, expected_revision=1,
        evidence={"plan_digest": "a" * 64})
    assert planned.status == EngineeringTaskStatus.PLANNED
    assert planned.revision == 2
    with pytest.raises(EngineeringTaskConflict, match="stale_revision"):
        tasks.transition(task.task_id, EngineeringTaskStatus.QUEUED,
            tenant_id=tenant, workspace_id=workspace, expected_revision=1)
    with pytest.raises(EngineeringTaskNotFound):
        tasks.get(task.task_id, tenant_id=tenant + "-other", workspace_id=workspace)


def test_postgres_workspace_and_plan_review_are_scope_bound_and_first_write_wins(repositories):
    tasks, workspaces, tenant, workspace = repositories
    task = tasks.create(make_task(tenant, workspace))
    record = workspaces.put(tenant_id=tenant, workspace_id=workspace, task_id=task.task_id,
        path="/managed/task-a", branch="kdo/task-a", base_revision="a" * 40,
        head_revision="a" * 40)
    assert workspaces.get(task.task_id, tenant_id=tenant, workspace_id=workspace) == record
    assert workspaces.list_scope(tenant_id=tenant, workspace_id=workspace) == [record]
    updated = workspaces.mark_status(task.task_id, tenant_id=tenant, workspace_id=workspace,
        status="awaiting_review", head_revision="b" * 40)
    assert updated.status == "awaiting_review"
    assert updated.head_revision == "b" * 40

    workspaces.record_plan_review(tenant_id=tenant, workspace_id=workspace, task_id=task.task_id,
        plan_digest="c" * 64, reviewer_id="reviewer-1", decision="approved", reason="checked")
    review = workspaces.get_plan_review(task.task_id, tenant_id=tenant, workspace_id=workspace)
    assert review["decision"] == "approved"
    assert review["reviewer_id"] == "reviewer-1"
    with pytest.raises(Exception, match="plan_review_already_recorded"):
        workspaces.record_plan_review(tenant_id=tenant, workspace_id=workspace, task_id=task.task_id,
            plan_digest="d" * 64, reviewer_id="reviewer-2", decision="rejected")
    with pytest.raises(EngineeringTaskNotFound):
        workspaces.get(task.task_id, tenant_id=tenant + "-other", workspace_id=workspace)
