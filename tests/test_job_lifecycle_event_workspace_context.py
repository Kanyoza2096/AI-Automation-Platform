"""Regression tests for tenant context on job lifecycle events."""

import asyncio
from types import SimpleNamespace

import pytest

from models.automation_job import AutomationJob, JobStatus
from services.job_service import JobService


class RecordingEventBus:
    def __init__(self):
        self.events = []

    async def publish(self, name, payload):
        self.events.append((name, payload))


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def insert(self, _data):
        return self

    def update(self, _data):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.row])


class FakeClient:
    def __init__(self, row):
        self.row = row

    def table(self, _name):
        return FakeQuery(self.row)


@pytest.mark.parametrize("creation", ["scheduled", "manual"])
def test_job_created_event_includes_workspace_id(monkeypatch, creation):
    async def run():
        if creation == "scheduled":
            row = AutomationJob.for_scheduled_slot(
                brand_id="brand-1",
                workspace_id="workspace-42",
                slot_date="2026-11-13",
                slot_hour=9,
                job_id="job-scheduled",
            ).to_db_dict()
        else:
            row = AutomationJob.for_manual_trigger(
                brand_id="brand-1",
                workspace_id="workspace-42",
                job_id="job-manual",
            ).to_db_dict()

        bus = RecordingEventBus()
        service = JobService(supabase_client=FakeClient(row), event_bus=bus)
        if creation == "scheduled":
            await service.create_scheduled_job(
                brand_id="brand-1",
                workspace_id="workspace-42",
                slot_date="2026-11-13",
                slot_hour=9,
                job_id="job-scheduled",
            )
        else:
            await service.create_manual_job(
                brand_id="brand-1",
                workspace_id="workspace-42",
                job_id="job-manual",
            )

        name, payload = bus.events[0]
        assert name == "job.created"
        assert payload["workspace_id"] == "workspace-42"

    asyncio.run(run())


def test_job_status_changed_event_includes_workspace_id():
    async def run():
        job = AutomationJob.for_manual_trigger(
            brand_id="brand-1",
            workspace_id="workspace-42",
            job_id="job-transition",
        )
        job.status = JobStatus.QUEUED
        bus = RecordingEventBus()
        service = JobService(
            supabase_client=FakeClient(job.to_db_dict()),
            event_bus=bus,
        )

        await service.transition(job, JobStatus.RUNNING)

        name, payload = bus.events[0]
        assert name == "job.status_changed"
        assert payload["workspace_id"] == "workspace-42"
        assert payload["old_status"] == "QUEUED"
        assert payload["new_status"] == "RUNNING"

    asyncio.run(run())
