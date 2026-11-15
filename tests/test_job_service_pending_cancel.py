import asyncio
from types import SimpleNamespace

from services.job_service import JobService


class _Query:
    def __init__(self, row):
        self.row = row
        self.filters = {}
        self.payload = None

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = values
        return self

    def select(self, _columns):
        return self

    def execute(self):
        row = self.row
        if not row:
            return SimpleNamespace(data=[])
        if row.get("job_id") != self.filters.get("job_id"):
            return SimpleNamespace(data=[])
        if row.get("workspace_id") != self.filters.get("workspace_id"):
            return SimpleNamespace(data=[])
        if row.get("status") not in self.filters.get("status", []):
            return SimpleNamespace(data=[])
        row.update(self.payload)
        return SimpleNamespace(data=[dict(row)])


class _Client:
    def __init__(self, row):
        self.row = row

    def table(self, _name):
        return _Query(self.row)


def test_cancel_pending_job_is_workspace_scoped_and_atomic():
    row = {
        "id": "db-id",
        "job_id": "job-1",
        "idempotency_key": "key-job-1",
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "status": "QUEUED",
    }
    service = JobService(supabase_client=_Client(row))

    cancelled = asyncio.run(service.cancel_pending_job("job-1", "workspace-a"))

    assert cancelled is not None
    assert cancelled.status.value == "CANCELLED"
    assert row["status"] == "CANCELLED"


def test_cancel_pending_job_does_not_cancel_running_or_other_workspace_jobs():
    running = {
        "id": "db-id",
        "job_id": "job-1",
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "status": "RUNNING",
    }
    service = JobService(supabase_client=_Client(running))
    assert asyncio.run(service.cancel_pending_job("job-1", "workspace-a")) is None
    assert running["status"] == "RUNNING"

    queued = {
        "id": "db-id",
        "job_id": "job-2",
        "idempotency_key": "key-job-2",
        "workspace_id": "workspace-b",
        "brand_id": "brand-b",
        "status": "QUEUED",
    }
    service = JobService(supabase_client=_Client(queued))
    assert asyncio.run(service.cancel_pending_job("job-2", "workspace-a")) is None
    assert queued["status"] == "QUEUED"
