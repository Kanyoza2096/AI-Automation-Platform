import asyncio

from models.automation_job import AutomationJob, JobStatus
from services.job_service import JobService


def test_manual_job_reuses_existing_idempotency_key(monkeypatch):
    existing = AutomationJob.for_manual_trigger(
        brand_id="brand-a",
        workspace_id="workspace-a",
        job_id="existing-job",
        idempotency_key="request-123",
    )
    existing.status = JobStatus.QUEUED
    service = JobService(supabase_client=object())

    async def find_by_key(key):
        assert key == "brand-a-manual-request-123"
        return existing

    monkeypatch.setattr(service, "find_by_idempotency_key", find_by_key)
    result = asyncio.run(service.create_manual_job(
        brand_id="brand-a",
        workspace_id="workspace-a",
        job_id="new-job-should-not-be-created",
        idempotency_key="request-123",
    ))

    assert result.job_id == "existing-job"
    assert result.status == JobStatus.QUEUED


def test_manual_job_idempotency_key_is_brand_scoped():
    first = AutomationJob.for_manual_trigger(
        brand_id="brand-a", workspace_id="workspace-a", job_id="job-a",
        idempotency_key="same-request",
    )
    second = AutomationJob.for_manual_trigger(
        brand_id="brand-b", workspace_id="workspace-b", job_id="job-b",
        idempotency_key="same-request",
    )

    assert first.idempotency_key == "brand-a-manual-same-request"
    assert second.idempotency_key == "brand-b-manual-same-request"
    assert first.idempotency_key != second.idempotency_key
