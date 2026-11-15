from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from models.automation_job import JobStatus, WorkflowType
from scheduler import brand_scheduler as scheduler_module


def test_manual_queue_processor_only_dispatches_owned_manual_jobs(monkeypatch):
    scheduler = object.__new__(scheduler_module.BrandScheduler)
    scheduler._job_service = object()
    scheduler.ai_profile = object()
    scheduler.brand = SimpleNamespace(id="brand-a", workspace_id="workspace-a", name="Brand A")
    scheduler._trigger_workflow = Mock(return_value=True)

    owned_manual = SimpleNamespace(
        job_id="manual-1", status=JobStatus.QUEUED,
        workflow_type=WorkflowType.MANUAL_PUBLISH, source="manual",
        brand_id="brand-a", workspace_id="workspace-a",
        scheduled_for=datetime.now(timezone.utc),
    )
    other_workspace = SimpleNamespace(
        job_id="manual-2", status=JobStatus.QUEUED,
        workflow_type=WorkflowType.MANUAL_PUBLISH, source="manual",
        brand_id="brand-a", workspace_id="workspace-b",
        scheduled_for=datetime.now(timezone.utc),
    )
    scheduled = SimpleNamespace(
        job_id="scheduled-1", status=JobStatus.QUEUED,
        workflow_type=WorkflowType.CONTENT_PUBLISH, source="scheduled",
        brand_id="brand-a", workspace_id="workspace-a",
        scheduled_for=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        scheduler_module, "_job_call",
        lambda *_args, **_kwargs: [owned_manual, other_workspace, scheduled],
    )

    processed = scheduler._process_queued_manual_jobs()

    assert processed == 1
    scheduler._trigger_workflow.assert_called_once_with(
        source="manual", existing_job=owned_manual,
    )
