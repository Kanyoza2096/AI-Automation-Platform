"""The autonomy API must report live OODA state, not historical counters."""
from types import SimpleNamespace

from routes.autonomy_routes import _resolve_phase


def test_idle_phase_does_not_stick_on_orient_after_historical_events():
    loop = SimpleNamespace(
        _running=True,
        _metrics=SimpleNamespace(events_processed=12, decisions_made=0),
    )
    assert _resolve_phase(loop) == "OBSERVE"


def test_idle_phase_does_not_stick_on_act_after_one_historical_decision():
    loop = SimpleNamespace(
        _running=True,
        _metrics=SimpleNamespace(events_processed=12, decisions_made=1),
    )
    assert _resolve_phase(loop) == "OBSERVE"


def test_explicit_live_phase_is_reported():
    loop = SimpleNamespace(_running=True, _current_phase="ORIENT")
    assert _resolve_phase(loop) == "ORIENT"


def test_stopped_loop_reports_observe():
    loop = SimpleNamespace(_running=False, _current_phase="ACT")
    assert _resolve_phase(loop) == "OBSERVE"
