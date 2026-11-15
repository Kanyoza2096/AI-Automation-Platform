import asyncio
from types import SimpleNamespace

import pytest

from models.automation_job import JobStatus
from scheduler.brand_scheduler import BrandScheduler
from services.job_service import JobService


def publish_result(output, *, step_ok=True):
    return SimpleNamespace(steps=[
        SimpleNamespace(step_name="publish", ok=step_ok, output=output),
    ])


def test_publish_success_requires_provider_post_id():
    result = publish_result({"ok": True, "status": "published"})
    assert BrandScheduler._extract_confirmed_post_id(result) is None


def test_flat_provider_receipt_returns_confirmed_post_id():
    result = publish_result({
        "ok": True,
        "status": "published",
        "platform_post_id": "fb-post-123",
    })
    assert BrandScheduler._extract_confirmed_post_id(result) == "fb-post-123"


def test_nested_provider_receipt_returns_confirmed_post_id():
    result = publish_result({
        "facebook": {"ok": True, "post_id": "fb-post-456"},
    })
    assert BrandScheduler._extract_confirmed_post_id(result) == "fb-post-456"


def test_failed_publish_step_never_returns_post_id():
    result = publish_result({
        "ok": True,
        "platform_post_id": "fb-post-789",
    }, step_ok=False)
    assert BrandScheduler._extract_confirmed_post_id(result) is None


def test_queued_publish_is_not_mistaken_for_completed_delivery():
    result = publish_result({
        "ok": True,
        "queued": True,
        "platform_post_id": "outbox-123",
    })
    assert BrandScheduler._extract_confirmed_post_id(result) is None


def test_job_service_refuses_published_without_provider_post_id():
    job = SimpleNamespace(status=JobStatus.RUNNING)
    service = object.__new__(JobService)

    with pytest.raises(ValueError, match="confirmed provider post ID"):
        asyncio.run(service.mark_published(job, post_id=None))
