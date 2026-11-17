"""Regression tests for Director state ownership boundaries."""
import pytest
from uuid import UUID

from services.director.models import Authority, Health, Observation, Risk, Sector, Task
from services.director.stores import InMemoryStore, _stable_observation_id


def _task(task_id: str, *, workspace_id=None, scope_type="platform") -> Task:
    return Task(
        id=task_id,
        sector=Sector.SELF,
        description="scope test",
        capability="review_content_quality",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        workspace_id=workspace_id,
        scope_type=scope_type,
    )


@pytest.mark.asyncio
async def test_workspace_tasks_are_visible_only_in_their_workspace_scope():
    store = InMemoryStore()
    await store.save_task(_task("workspace-a-task", workspace_id="workspace-a"))
    await store.save_task(_task("workspace-b-task", workspace_id="workspace-b"))

    a_rows = await store.list_tasks(scope_type="workspace", workspace_id="workspace-a")
    b_rows = await store.list_tasks(scope_type="workspace", workspace_id="workspace-b")
    platform_rows = await store.list_tasks(scope_type="platform")

    assert [row["id"] for row in a_rows] == ["workspace-a-task"]
    assert [row["id"] for row in b_rows] == ["workspace-b-task"]
    assert platform_rows == []


@pytest.mark.asyncio
async def test_legacy_unclassified_tasks_are_not_promoted_to_platform_scope():
    store = InMemoryStore()
    await store.save_task(_task("legacy-task", scope_type="legacy_unclassified"))

    assert await store.list_tasks(scope_type="platform") == []
    legacy = await store.list_tasks(scope_type="legacy_unclassified")
    assert [row["id"] for row in legacy] == ["legacy-task"]


def test_observation_workspace_ownership_is_explicit():
    observation = Observation(
        source="workspace-monitor",
        sector=Sector.INFRA,
        status=Health.UNKNOWN,
        workspace_id="workspace-a",
    )

    assert observation.workspace_id == "workspace-a"
    assert observation.scope_type == "workspace"


def test_platform_observations_remain_platform_scoped_by_default():
    observation = Observation(source="platform-monitor", sector=Sector.INFRA)
    assert observation.workspace_id is None
    assert observation.scope_type == "platform"


def test_observation_identity_is_stable_uuid_text_for_existing_schema():
    fingerprint = "a" * 64
    first = _stable_observation_id(fingerprint)
    second = _stable_observation_id(fingerprint)

    assert first == second
    assert str(UUID(first)) == first
    assert first != _stable_observation_id("b" * 64)


def test_unverified_legacy_learning_does_not_qualify_for_replay():
    from services.director.core import Director

    legacy = {
        "id": "legacy-learning",
        "task_id": "legacy-task",
        "capability": "review_content_quality",
        "sector": "self",
        "outcome": "success",
        "context": {"status": "completed"},
    }

    assert Director._is_replayable_learning_signal(legacy) is False


def test_verified_learning_requires_both_durable_and_outcome_verification():
    from services.director.core import Director

    verified = {
        "id": "learn-verified",
        "task_id": "task-verified",
        "capability": "review_content_quality",
        "sector": "self",
        "outcome": "success",
        "context": {"status": "verified"},
    }

    assert Director._is_replayable_learning_signal(verified) is True


def test_learning_without_task_identity_cannot_restore_idempotency():
    from services.director.core import Director

    verified_without_task = {
        "id": "learn-no-task",
        "capability": "review_content_quality",
        "sector": "self",
        "outcome": "success",
        "context": {"status": "verified"},
        "verification_status": "verified",
    }

    assert Director._is_replayable_learning_signal(verified_without_task) is False
