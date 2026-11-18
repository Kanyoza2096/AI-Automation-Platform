"""Regression coverage for evidence-backed diagnosis of failed automation jobs."""

import asyncio
from types import SimpleNamespace

from services.job_service import JobService
from services.job_stats_service import JobStatsService
from services.reliability.diagnostician import Diagnostician
from services.reliability.models import Incident
from services.workforce.consumers.heal_command_consumer import HealCommandConsumer


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.requested_limit = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.requested_limit = value
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows):
        self.query = _Query(rows)

    def table(self, name):
        assert name == "automation_jobs"
        return self.query


class _SharedClient:
    def __init__(self, client):
        self.client = client

    def execute(self, operation):
        return operation(self.client)


def test_failure_diagnostics_reads_failed_rows_and_excludes_unneeded_payload():
    rows = [{
        "job_id": "pub-test-1",
        "brand_id": "brand-test",
        "platform": "facebook",
        "source": "scheduled",
        "workflow_type": "content_publish",
        "attempt_count": 1,
        "max_attempts": 3,
        "last_error": "Steps failed: ['generate_content']",
        "last_error_code": "WORKFLOW_FAILED",
        "platform_post_id": None,
        "updated_at": "2026-11-18T10:00:00Z",
        "result": {
            "draft": "DO_NOT_EXPOSE_FULL_DRAFT",
            "failed_steps": [{
                "step": "generate_content",
                "error_code": "AI_VALIDATION_ERROR",
                "error": "caption_quality: too short: 54 words (min 80)",
                "detail": {"exception_type": "AIValidationError"},
            }],
        },
    }]
    client = _Client(rows)
    service = JobStatsService()
    service._get_shared_client = lambda: _SharedClient(client)

    diagnostics = service.get_failure_diagnostics(limit=20)

    assert diagnostics["status"] == "available"
    assert diagnostics["total_returned"] == 1
    assert diagnostics["counts_by_category"] == {"caption_length_failure": 1}
    assert diagnostics["jobs"][0]["job_id"] == "pub-test-1"
    assert diagnostics["jobs"][0]["step_error_code"] == "AI_VALIDATION_ERROR"
    assert diagnostics["jobs"][0]["has_confirmed_platform_post"] is False
    assert client.query.requested_limit == 20
    assert "DO_NOT_EXPOSE_FULL_DRAFT" not in str(diagnostics)


def test_job_service_health_carries_failed_job_evidence(monkeypatch):
    import services.job_stats_service as module

    class FakeStats:
        def health_check(self):
            return {
                "status": "degraded",
                "stats": {"status_counts": {"FAILED": 12}},
                "reason": "job backlog growing",
            }

        def get_failure_diagnostics(self, limit=20):
            assert limit == 20
            return {
                "status": "available",
                "total_returned": 2,
                "counts_by_category": {"caption_length_failure": 2},
                "jobs": [{"job_id": "pub-1"}, {"job_id": "pub-2"}],
            }

    monkeypatch.setattr(module, "get_job_stats_service", lambda: FakeStats())
    result = JobService().health_check()

    assert result["status"] == "degraded"
    assert result["failure_diagnostics"]["total_failed"] == 12
    assert result["failure_diagnostics"]["truncated"] is True
    assert len(result["failure_diagnostics"]["jobs"]) == 2


def test_reliability_diagnostician_uses_job_failure_metadata():
    incident = Incident(
        symptom="job_service degraded",
        source="engine_alert",
        component="job_service",
    )
    evidence = {
        "health_registry": {
            "job_service": {
                "status": "degraded",
                "metadata": {
                    "failure_diagnostics": {
                        "status": "available",
                        "total_failed": 12,
                        "total_returned": 12,
                        "truncated": False,
                        "counts_by_category": {
                            "evidence_or_citation_failure": 3,
                            "caption_length_failure": 4,
                            "first_person_policy_failure": 2,
                            "semantic_review_failure": 3,
                        },
                        "jobs": [
                            {"step_error_code": "AI_VALIDATION_ERROR"},
                            {"step_error_code": "AI_VALIDATION_ERROR"},
                        ],
                    }
                },
            }
        }
    }

    diagnosis = Diagnostician().diagnose(incident, evidence)

    assert diagnosis.probable_cause == "job_content_validation_backlog"
    assert diagnosis.confidence >= 0.9
    assert "Persisted automation_jobs evidence: 12 FAILED job(s)" in diagnosis.evidence_summary
    assert any("Failure categories:" in item for item in diagnosis.evidence_summary)


def test_unmapped_job_service_heal_does_not_fall_back_to_generic_restart():
    class FakeBus:
        async def subscribe(self, *_args, **_kwargs):
            return None

    consumer = HealCommandConsumer(bus=FakeBus())
    dispatched = []

    async def fake_dispatch(command_id, body):
        dispatched.append((command_id, body))
        return {"transaction_id": "should-not-be-created"}

    consumer._dispatch = fake_dispatch
    asyncio.run(consumer._on_heal_command(event={
        "payload": {
            "engine": "job_service",
            "reason": "job_service degraded",
            "workspace_id": "platform",
        }
    }))

    assert dispatched == []
    assert consumer._events_handled == 1
