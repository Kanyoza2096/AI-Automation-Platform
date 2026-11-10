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

    assert signal is not None
    assert signal.outcome == "unverified"
    assert loop.capability_stats["run_health_scan"]["successes"] == 0
    assert loop.capability_stats["run_health_scan"]["failures"] == 0
    assert loop.capability_stats["run_health_scan"]["unverified"] == 1
    assert loop.get_success_rate("run_health_scan") is None
    assert loop.get_recent_success_rate("run_health_scan") is None
    assert loop.verification_stats["run_health_scan"]["unverified"] == 1


def test_only_verified_success_and_real_failures_contribute_to_success_rate():
    loop = LearningLoop()

    loop.record_outcome(task(Status.COMPLETED), duration_ms=100)
    loop.record_outcome(task(Status.VERIFIED), duration_ms=110)
    loop.record_outcome(task(Status.FAILED, error="network timeout"), duration_ms=130)

    assert loop.capability_stats["run_health_scan"]["successes"] == 1
    assert loop.capability_stats["run_health_scan"]["failures"] == 1
    assert loop.capability_stats["run_health_scan"]["unverified"] == 1
    assert loop.get_success_rate("run_health_scan") == 0.5
    report = loop.get_learning_report()["capabilities"]["run_health_scan"]
    assert report["verified_outcome_count"] == 2
    assert report["unverified_outcome_count"] == 1
    assert report["total"] == 3


def test_many_unverified_completions_cannot_trigger_skip_or_escalation_learning():
    loop = LearningLoop()
    for _ in range(10):
        loop.record_outcome(task(Status.COMPLETED), duration_ms=100, target="worker-pool-a")

    assert loop.get_context_success_rate(Sector.INFRA, "run_health_scan", "worker-pool-a") is None
    assert loop.should_skip(Sector.INFRA, "run_health_scan", "worker-pool-a") is False
    assert loop.should_escalate(Sector.INFRA, "run_health_scan", "worker-pool-a") is False
    assert loop.target_failure_counts.get("run_health_scan:worker-pool-a", 0) == 0


def test_replay_reclassifies_legacy_completed_success_as_unverified():
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

    assert loop.rebuild_from_signals([legacy_signal]) == 1
    assert loop.capability_stats["run_health_scan"]["successes"] == 0
    assert loop.capability_stats["run_health_scan"]["unverified"] == 1
    assert loop.get_success_rate("run_health_scan") is None
    assert loop.verification_stats["run_health_scan"]["unverified"] == 1


def test_replay_accepts_explicit_unverified_outcome_without_resetting_failures():
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

    assert loop.rebuild_from_signals([signal]) == 1
    assert loop.target_failure_counts["run_health_scan:worker-pool-a"] == 2
    assert loop.target_last_error["run_health_scan:worker-pool-a"] == "NETWORK_TIMEOUT"


def test_verified_success_resets_failure_window_without_losing_unverified_counter():
    loop = LearningLoop()
    loop.target_failure_counts["run_health_scan:worker-pool-a"] = 1
    loop.target_last_error["run_health_scan:worker-pool-a"] = "NETWORK_TIMEOUT"

    loop.record_outcome(task(Status.VERIFIED), duration_ms=100, target="worker-pool-a")
    signal = loop.record_outcome(task(Status.COMPLETED), duration_ms=100, target="worker-pool-a")

    assert signal is not None
    assert signal.outcome == "unverified"
    assert loop.context_stats["infra:run_health_scan:worker-pool-a"]["unverified"] == 1
    assert loop.time_stats[f"infra:run_health_scan:worker-pool-a:{loop._get_time_context()}"]["unverified"] == 1


def test_learning_report_preserves_unknown_rates_when_no_verified_samples_exist():
    loop = LearningLoop()
    loop.record_outcome(task(Status.COMPLETED), duration_ms=120)

    report = loop.get_learning_report()["capabilities"]["run_health_scan"]

    assert report["success_rate"] is None
    assert report["recent_rate"] is None
    assert report["verified_outcome_count"] == 0
    assert report["unverified_outcome_count"] == 1


def test_recent_unverified_attempts_do_not_borrow_an_old_global_success_rate():
    from datetime import datetime, timedelta, timezone

    loop = LearningLoop()
    loop.record_outcome(task(Status.VERIFIED), duration_ms=100)
    loop.signals[-1].timestamp = datetime.now(timezone.utc) - timedelta(days=10)
    loop.record_outcome(task(Status.COMPLETED), duration_ms=100)

    assert loop.get_success_rate("run_health_scan") == 1.0
    assert loop.get_recent_success_rate("run_health_scan") is None
    report = loop.get_learning_report()["capabilities"]["run_health_scan"]
    assert report["success_rate"] == 1.0
    assert report["recent_rate"] is None
