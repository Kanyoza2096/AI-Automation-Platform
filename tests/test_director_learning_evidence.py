"""Director learning must distinguish verified outcomes from handler-reported completion."""

from services.director.analysis import LearningLoop
from services.director.models import Authority, Risk, Sector, Status, Task


def task(status, *, error=None):
    return Task(
        id=f"task-{status.value}",
        sector=Sector.INFRA,
        description="Exercise a governed capability",
        capability="run_health_scan",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=status,
        error=error,
    )


def test_completed_without_verification_is_not_counted_as_success():
    loop = LearningLoop()

    signal = loop.record_outcome(task(Status.COMPLETED), duration_ms=120)

    assert signal is None
    assert loop.capability_stats == {}
    assert loop.get_success_rate("run_health_scan") is None
    assert loop.get_recent_success_rate("run_health_scan") is None
    assert loop.verification_stats == {}


def test_only_verified_success_and_real_failures_contribute_to_success_rate():
    loop = LearningLoop()

    loop.record_outcome(task(Status.COMPLETED), duration_ms=100)
    loop.record_outcome(task(Status.VERIFIED), duration_ms=110)
    loop.record_outcome(task(Status.FAILED, error="network timeout"), duration_ms=130)

    assert loop.capability_stats["run_health_scan"]["successes"] == 1
    assert loop.capability_stats["run_health_scan"]["failures"] == 0
    assert loop.get_success_rate("run_health_scan") == 1.0
    report = loop.get_learning_report()["capabilities"]["run_health_scan"]
    assert report["verified_outcome_count"] == 1
    assert report["unverified_outcome_count"] == 0
    assert report["total"] == 1


def test_authoritatively_ineffective_outcome_counts_as_verified_failure():
    loop = LearningLoop()
    ineffective = task(Status.INEFFECTIVE)

    signal = loop.record_outcome(
        ineffective, duration_ms=140, target="worker-pool-a"
    )

    assert signal is not None
    assert signal.outcome == "failure"
    assert signal.context["status"] == Status.INEFFECTIVE.value
    assert signal.context["error_class"] == "AUTHORITATIVE_STATE_MISMATCH"
    assert loop.verification_stats["run_health_scan"]["ineffective"] == 1
    assert loop.capability_stats["run_health_scan"]["failures"] == 1
    assert loop.capability_stats["run_health_scan"]["successes"] == 0
    assert loop.target_failure_counts["run_health_scan:worker-pool-a"] == 1
    assert loop.target_last_error["run_health_scan:worker-pool-a"] == "AUTHORITATIVE_STATE_MISMATCH"
    assert loop.get_success_rate("run_health_scan") == 0.0


def test_repeated_verified_ineffectiveness_informs_existing_escalation_and_skip_gates():
    loop = LearningLoop()
    target = "worker-pool-a"

    for index in range(3):
        ineffective = task(Status.INEFFECTIVE)
        ineffective.id = f"ineffective-{index}"
        loop.record_outcome(ineffective, duration_ms=140, target=target)

    assert loop.get_context_success_rate(Sector.INFRA, "run_health_scan", target) == 0.0
    assert loop.should_escalate(Sector.INFRA, "run_health_scan", target) is True
    assert loop.should_skip(Sector.INFRA, "run_health_scan", target) is True
    assert len(loop.failure_patterns["run_health_scan"]) == 3
    assert loop.adjust_confidence(task(Status.COMPLETED), target=target) < 0.5


def test_ordinary_failed_and_unknown_tasks_do_not_create_verified_learning():
    loop = LearningLoop()

    assert loop.record_outcome(task(Status.FAILED, error="network timeout"), 100) is None
    assert loop.record_outcome(task(Status.FLAGGED), 100) is None
    assert loop.capability_stats == {}
    assert loop.verification_stats == {}


def test_many_unverified_completions_cannot_trigger_skip_or_escalation_learning():
    loop = LearningLoop()
    for _ in range(10):
        loop.record_outcome(task(Status.COMPLETED), duration_ms=100, target="worker-pool-a")

    assert loop.get_context_success_rate(Sector.INFRA, "run_health_scan", "worker-pool-a") is None
    assert loop.should_skip(Sector.INFRA, "run_health_scan", "worker-pool-a") is False
    assert loop.should_escalate(Sector.INFRA, "run_health_scan", "worker-pool-a") is False
    assert loop.target_failure_counts.get("run_health_scan:worker-pool-a", 0) == 0


def test_replay_rejects_legacy_completed_success_without_verification():
    loop = LearningLoop()
    legacy_signal = {
        "capability": "run_health_scan",
        "sector": "infra",
        "outcome": "success",
        "duration_ms": 90,
        "error_type": None,
        "timestamp": "2026-11-10T17:00:00+00:00",
        "context": {
            "status": Status.COMPLETED.value,
            "target": "worker-pool-a",
        },
    }

    assert loop.rebuild_from_signals([legacy_signal]) == 0
    assert loop.capability_stats == {}
    assert loop.get_success_rate("run_health_scan") is None
    assert loop.verification_stats == {}


def test_replay_rejects_unverified_outcome_without_resetting_failures():
    loop = LearningLoop()
    loop.target_failure_counts["run_health_scan:worker-pool-a"] = 2
    loop.target_last_error["run_health_scan:worker-pool-a"] = "NETWORK_TIMEOUT"
    signal = {
        "capability": "run_health_scan",
        "sector": "infra",
        "outcome": "unverified",
        "duration_ms": 90,
        "timestamp": "2026-11-10T17:00:00+00:00",
        "context": {"status": Status.COMPLETED.value, "target": "worker-pool-a"},
    }

    assert loop.rebuild_from_signals([signal]) == 0
    assert loop.target_failure_counts["run_health_scan:worker-pool-a"] == 2
    assert loop.target_last_error["run_health_scan:worker-pool-a"] == "NETWORK_TIMEOUT"


def test_verified_success_resets_failure_window_and_unverified_completion_is_ignored():
    loop = LearningLoop()
    loop.target_failure_counts["run_health_scan:worker-pool-a"] = 1
    loop.target_last_error["run_health_scan:worker-pool-a"] = "NETWORK_TIMEOUT"

    loop.record_outcome(task(Status.VERIFIED), duration_ms=100, target="worker-pool-a")
    signal = loop.record_outcome(task(Status.COMPLETED), duration_ms=100, target="worker-pool-a")

    assert signal is None
    assert loop.target_failure_counts.get("run_health_scan:worker-pool-a", 0) == 0
    assert "run_health_scan:worker-pool-a" not in loop.target_last_error


def test_learning_report_preserves_unknown_rates_when_no_verified_samples_exist():
    loop = LearningLoop()
    loop.record_outcome(task(Status.COMPLETED), duration_ms=120)

    report = loop.get_learning_report()["capabilities"]

    assert "run_health_scan" not in report
    assert loop.get_success_rate("run_health_scan") is None


def test_absent_unverified_attempt_signal_does_not_change_old_global_rate():
    from datetime import datetime, timedelta, timezone

    loop = LearningLoop()
    loop.record_outcome(task(Status.VERIFIED), duration_ms=100)
    loop.signals[-1].timestamp = datetime.now(timezone.utc) - timedelta(days=10)
    loop.record_outcome(task(Status.COMPLETED), duration_ms=100)

    assert loop.get_success_rate("run_health_scan") == 1.0
    assert loop.get_recent_success_rate("run_health_scan") == 1.0
    report = loop.get_learning_report()["capabilities"]["run_health_scan"]
    assert report["success_rate"] == 1.0
    assert report["recent_rate"] == 1.0
