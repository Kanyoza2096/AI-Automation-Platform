"""P16: engine health publishers expose the Director signal contract honestly."""
import asyncio

from core.director_signal_contract import director_signal_metrics
from services.job_service import JobService
from services.meta_engine.core import MetaEngineV7
from core.autonomous_content import AutonomousContentEngine


def test_signal_contract_rejects_non_list_payloads():
    try:
        director_signal_metrics({"capability": "fake"})
    except TypeError:
        pass
    else:
        raise AssertionError("non-list signal payload must be rejected")


def test_signal_contract_marks_empty_recommendations_explicitly():
    assert director_signal_metrics() == {
        "director_signals": [],
        "director_signal_contract": "metrics.director_signals/v1",
    }


def test_job_service_unknown_health_stays_unknown_and_publishes_contract(monkeypatch):
    class Stats:
        def health_check(self):
            return {"status": "unknown", "reason": "stats_unavailable"}

    import services.job_stats_service as module
    monkeypatch.setattr(module, "get_job_stats_service", lambda: Stats())
    result = JobService().health_check()
    assert result["status"] == "unknown"
    assert result["metrics"]["director_signals"] == []
    assert result["metrics"]["director_signal_contract"] == "metrics.director_signals/v1"


def test_meta_engine_health_publishes_contract_without_claiming_health():
    meta = MetaEngineV7()
    result = meta.check_health()
    assert result["status"] in {"starting", "stopped"}
    assert result["metrics"]["director_signals"] == []


def test_autonomous_content_health_reports_actual_running_state():
    engine = AutonomousContentEngine()
    result = engine.check_health()
    assert result["status"] == "stopped"
    assert result["running"] is False
    assert result["metrics"]["director_signals"] == []
