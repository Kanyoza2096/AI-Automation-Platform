from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from models.automation_job import JobStatus, WorkflowType
from scheduler.brand_scheduler import BrandScheduler


class FakeJobService:
    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = []

    def find_by_brand(self, brand_id, status=None, limit=50, *, oldest_first=False):
        self.calls.append((brand_id, status, limit, oldest_first))
        return self.jobs


def make_job(**overrides):
    now = datetime.now(timezone.utc)
    slot = now - timedelta(hours=2)
    values = {
        "job_id": "pub_due_slot",
        "brand_id": "brand-a",
        "workspace_id": "workspace-a",
        "status": JobStatus.QUEUED,
        "workflow_type": WorkflowType.CONTENT_PUBLISH,
        "source": "scheduled",
        "scheduled_for": slot,
        "idempotency_key": f"brand-a-{slot.date().isoformat()}-{slot.hour:02d}-content",
        "created_at": slot,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_scheduler(jobs):
    brand = SimpleNamespace(
        id="brand-a",
        name="Brand A",
        workspace_id="workspace-a",
        timezone="UTC",
        posting_schedule={},
        status="active",
    )
    service = FakeJobService(jobs)
    scheduler = BrandScheduler(
        brand=brand,
        ai_profile=SimpleNamespace(name="Test profile"),
        container=object(),
        job_service=service,
    )
    scheduler._trigger_workflow = Mock(return_value=True)
    return scheduler, service


def test_due_queued_scheduled_job_is_resumed_through_idempotent_slot():
    job = make_job()
    scheduler, service = make_scheduler([job])

    accepted = scheduler._process_queued_automation_jobs()

    assert accepted == 1
    assert service.calls == [("brand-a", JobStatus.QUEUED, 50, True)]
    scheduler._trigger_workflow.assert_called_once()
    kwargs = scheduler._trigger_workflow.call_args.kwargs
    assert kwargs["source"] == "queued_retry"
    assert kwargs["today"].isoformat() == job.scheduled_for.date().isoformat()
    assert kwargs["hour"] == job.scheduled_for.hour


def test_future_queued_job_is_not_started_early():
    job = make_job(scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1))
    scheduler, _ = make_scheduler([job])

    assert scheduler._process_queued_automation_jobs() == 0
    scheduler._trigger_workflow.assert_not_called()


def test_wrong_workspace_or_non_scheduled_jobs_are_not_consumed():
    wrong_workspace = make_job(job_id="wrong_workspace", workspace_id="workspace-b")
    manual = make_job(job_id="manual", source="manual")
    wrong_type = make_job(job_id="news", workflow_type=WorkflowType.NEWS_PUBLISH)
    scheduler, _ = make_scheduler([wrong_workspace, manual, wrong_type])

    assert scheduler._process_queued_automation_jobs() == 0
    scheduler._trigger_workflow.assert_not_called()


def test_invalid_slot_key_is_ignored():
    job = make_job(idempotency_key="brand-a-not-a-slot")
    scheduler, _ = make_scheduler([job])

    assert scheduler._process_queued_automation_jobs() == 0
    scheduler._trigger_workflow.assert_not_called()


def test_unaccepted_workflow_is_not_counted_as_resumed():
    scheduler, _ = make_scheduler([make_job()])
    scheduler._trigger_workflow.return_value = False

    assert scheduler._process_queued_automation_jobs() == 0
    scheduler._trigger_workflow.assert_called_once()


def test_missing_job_service_is_a_noop():
    brand = SimpleNamespace(
        id="brand-a", name="Brand A", workspace_id="workspace-a",
        timezone="UTC", posting_schedule={}, status="active",
    )
    scheduler = BrandScheduler(
        brand=brand, ai_profile=SimpleNamespace(name="Test"),
        container=object(), job_service=None,
    )

    assert scheduler._process_queued_automation_jobs() == 0



def test_workflow_is_not_executed_when_job_transition_to_running_fails():
    job = make_job()
    scheduler, service = make_scheduler([job])
    service.find_by_idempotency_key = Mock(return_value=job)
    service.transition = Mock(side_effect=RuntimeError("database unavailable"))
    scheduler._trigger_workflow = BrandScheduler._trigger_workflow.__get__(
        scheduler, BrandScheduler
    )
    scheduler._container = Mock()

    assert scheduler._trigger_workflow(
        source="queued_retry",
        today=job.scheduled_for.date(),
        hour=job.scheduled_for.hour,
    ) is False
    service.transition.assert_called_once()
    scheduler._container.resolve.assert_not_called()

def test_global_publish_lock_released_before_optional_notifications():
    job = make_job()
    scheduler, _ = make_scheduler([job])
    lock = SimpleNamespace(
        acquire=Mock(return_value=True),
        release=Mock(),
        is_degraded_local=False,
    )
    semaphore = SimpleNamespace(
        acquire=Mock(return_value=True),
        release=Mock(),
    )
    scheduler._distributed_lock = lock
    scheduler._publish_semaphore = semaphore
    scheduler._container = Mock()
    scheduler._container.resolve.return_value = lambda **kwargs: object()
    scheduler._execute_workflow = Mock(return_value=SimpleNamespace(steps=[
        SimpleNamespace(step_name="select_topic", ok=True, output={"topic": "Test"}),
        SimpleNamespace(
            step_name="publish", ok=True,
            output={"ok": True, "status": "published", "post_id": "provider-post-1"},
        ),
    ]))
    scheduler._emit = Mock()

    def assert_released(*args, **kwargs):
        payload = args[1] if len(args) > 1 else kwargs
        if payload.get("stage") == "Complete":
            assert lock.release.call_count == 1
            assert semaphore.release.call_count == 1

    scheduler._emit_dashboard = Mock(side_effect=assert_released)
    scheduler._notify_webhooks = Mock(side_effect=assert_released)
    scheduler._metrics.record_publish_success = Mock()

    assert scheduler._trigger_workflow(source="manual") is True
    assert lock.release.call_count == 1
    assert semaphore.release.call_count == 1
