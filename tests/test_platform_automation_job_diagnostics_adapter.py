import asyncio

import pytest

from services.director.orchestration.automation_job_diagnostics_adapter import (
    AutomationJobDiagnosticsMissionAdapter,
)
from services.director.orchestration.contracts import MissionTaskContext


class FakeJobStats:
    def __init__(self, intelligence):
        self.intelligence = intelligence
        self.calls = []

    def get_failure_intelligence(self, limit=100):
        self.calls.append(limit)
        return self.intelligence


def _context(**overrides):
    values = {
        "task_id": "platform-task-1",
        "mission_id": "platform-mission-1",
        "workspace_id": None,
        "scope_type": "platform",
        "capability": "automation.jobs.diagnose",
        "objective": "Diagnose the timeout failure category.",
        "inputs": {
            "failure_category": "provider_timeout",
            "job_ids": ["job-1", "job-2"],
        },
        "correlation_id": "recovery-correlation-1",
        "attempt_id": "agent-attempt-1",
    }
    values.update(overrides)
    return MissionTaskContext(**values)


def test_platform_job_diagnostics_returns_fresh_read_only_evidence():
    stats = FakeJobStats({
        "status": "available",
        "categories": {
            "provider_timeout": {
                "count": 2,
                "job_ids": ["job-1", "job-2", "job-3"],
                "recovery_proposals": [
                    {"job_id": "job-1", "recommended_action": "inspect_provider"},
                    {"job_id": "job-2", "recommended_action": "inspect_provider"},
                    {"job_id": "job-3", "recommended_action": "ignore"},
                ],
                "failure_samples": [
                    {"job_id": "job-1", "last_error_code": "AI_PROVIDER_UNAVAILABLE", "failed_steps": ["generate"], "attempt_count": 2, "max_attempts": 3},
                    {"job_id": "job-2", "last_error_code": "AI_PROVIDER_UNAVAILABLE", "failed_steps": ["render"], "attempt_count": 1, "max_attempts": 3},
                    {"job_id": "job-3", "last_error_code": "IGNORE_ME", "failed_steps": ["publish"], "attempt_count": 1, "max_attempts": 3},
                ],
            }
        },
    })
    adapter = AutomationJobDiagnosticsMissionAdapter(job_stats_service=stats)

    result = asyncio.run(adapter.invoke(_context()))

    assert stats.calls == [500]
    assert result.status == "completed"
    assert result.scope_type == "platform"
    assert result.workspace_id is None
    assert result.authority == "none"
    assert result.side_effect_class == "read_only"
    assert result.source_metadata["automatic_retry"] is False
    assert result.source_metadata["publication_replay_allowed"] is False
    assert result.source_metadata["inspected_job_ids"] == ["job-1", "job-2"]
    assert any("AI_PROVIDER_UNAVAILABLE" in item for item in result.findings)
    assert any("generate" in item for item in result.findings)
    assert not any("IGNORE_ME" in item for item in result.findings)
    assert all("retry" not in item.lower() for item in result.findings)


def test_platform_job_diagnostics_fails_closed_when_snapshot_unavailable():
    stats = FakeJobStats({"status": "unavailable"})
    adapter = AutomationJobDiagnosticsMissionAdapter(job_stats_service=stats)

    result = asyncio.run(adapter.invoke(_context()))

    assert result.status == "unavailable"
    assert result.authority == "none"
    assert result.workspace_id is None


def test_platform_job_diagnostics_rejects_workspace_scoped_context():
    stats = FakeJobStats({"status": "available", "categories": {}})
    adapter = AutomationJobDiagnosticsMissionAdapter(job_stats_service=stats)

    with pytest.raises(ValueError, match="requires_platform_scope"):
        asyncio.run(adapter.invoke(_context(
            scope_type="workspace",
            workspace_id="workspace-1",
        )))
