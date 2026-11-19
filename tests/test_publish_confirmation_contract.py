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


def test_nested_provider_receipt_preserves_page_and_account_identity():
    result = publish_result({
        "facebook": {
            "ok": True,
            "post_id": "fb-post-789",
            "page_id": "page-123",
            "account_id": "account-456",
        },
    })
    assert BrandScheduler._extract_publish_receipt(result) == {
        "platform_post_id": "fb-post-789",
        "page_id": "page-123",
        "account_id": "account-456",
        "platform": "facebook",
    }


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

def test_manual_publish_fails_closed_without_durable_job_service():
    scheduler = BrandScheduler.__new__(BrandScheduler)
    scheduler._job_service = None

    result = scheduler.trigger_manual()

    assert result["ok"] is False
    assert result["error"] == (
        "Manual publishing requires durable JobService persistence"
    )



def test_mark_published_persists_provider_receipt_fields():
    from models.automation_job import AutomationJob

    class Query:
        def __init__(self, row):
            self.row = row
            self.update_data = {}

        def update(self, data):
            self.update_data = dict(data)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            self.row.update(self.update_data)
            return SimpleNamespace(data=[dict(self.row)])

    class Table:
        def __init__(self, row):
            self.row = row

        def update(self, data):
            return Query(self.row).update(data)

    class Client:
        def __init__(self, row):
            self.row = row

        def table(self, _name):
            return Table(self.row)

    job = AutomationJob(
        id="job-receipt-1",
        job_id="pub-receipt-1",
        idempotency_key="receipt-key-1",
        workspace_id="workspace-1",
        brand_id="brand-1",
        status=JobStatus.PUBLISHING,
    )
    row = job.to_db_dict()
    service = JobService(supabase_client=Client(row))
    receipt_result = {
        "receipt": {
            "platform_post_id": "fb-post-123",
            "page_id": "page-123",
            "account_id": "account-456",
        }
    }

    saved = asyncio.run(service.mark_published(
        job,
        post_id="fb-post-123",
        social_account_id="account-456",
        result=receipt_result,
    ))

    assert saved.status == JobStatus.PUBLISHED
    assert row["platform_post_id"] == "fb-post-123"
    assert row["social_account_id"] == "account-456"
    assert row["result"] == receipt_result


def test_mark_published_repairs_legacy_published_row_missing_receipt():
    from models.automation_job import AutomationJob

    class Query:
        def __init__(self, row):
            self.row = row
            self.update_data = {}

        def update(self, data):
            self.update_data = dict(data)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            self.row.update(self.update_data)
            return SimpleNamespace(data=[dict(self.row)])

    class Table:
        def __init__(self, row):
            self.row = row

        def update(self, data):
            return Query(self.row).update(data)

    class Client:
        def __init__(self, row):
            self.row = row

        def table(self, _name):
            return Table(self.row)

    job = AutomationJob(
        id="job-receipt-2",
        job_id="pub-receipt-2",
        idempotency_key="receipt-key-2",
        workspace_id="workspace-1",
        brand_id="brand-1",
        status=JobStatus.PUBLISHED,
    )
    row = job.to_db_dict()
    service = JobService(supabase_client=Client(row))

    saved = asyncio.run(service.mark_published(
        job,
        post_id="fb-post-recovered",
        social_account_id="account-789",
        result={"receipt": {"platform_post_id": "fb-post-recovered"}},
    ))

    assert saved.platform_post_id == "fb-post-recovered"
    assert row["platform_post_id"] == "fb-post-recovered"
    assert row["social_account_id"] == "account-789"
