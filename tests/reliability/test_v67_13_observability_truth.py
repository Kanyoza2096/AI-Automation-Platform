from services.v67_reliability_observability import build_reliability_snapshot, validate_reliability_snapshot


def test_uncertain_execution_creates_reconciliation_debt_not_failure_or_success():
    s = build_reliability_snapshot([
        {"status": "succeeded"}, {"status": "uncertain"}, {"status": "failed"}
    ])
    assert s["execution"]["known_success_rate"] == 0.5
    assert s["execution"]["uncertain"] == 1
    assert s["status"] == "attention_required"
    assert s["recovery"]["reconciliation_debt"] == 1


def test_recovery_open_and_planned_are_visible_debt():
    s = build_reliability_snapshot([], [{"state": "open"}, {"state": "recovery_planned"}])
    assert s["status"] == "attention_required"
    assert s["recovery"]["reconciliation_debt"] == 2


def test_clean_snapshot_is_healthy():
    s = build_reliability_snapshot([
        {"status": "succeeded"}, {"status": "failed"}, {"status": "cancelled"}
    ])
    validate_reliability_snapshot(s)
    assert s["status"] == "healthy"
    assert s["execution"]["known_success_rate"] == 1 / 3


def test_unknown_telemetry_state_fails_closed():
    s = build_reliability_snapshot([{"status": "bogus"}])
    assert s["status"] == "invalid"
    validate_reliability_snapshot(s)
