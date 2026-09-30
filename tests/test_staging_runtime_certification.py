from scripts.staging_runtime_certification import evidence_is_authoritative, run_preflight, RuntimeConfig


def test_evidence_requires_terminal_status_and_provider_object():
    assert evidence_is_authoritative({"status": "succeeded", "provider_object_id": "fb-1"})
    assert evidence_is_authoritative({"status": "queued", "provider_object_id": "fb-1"}) is False
    assert evidence_is_authoritative({"status": "succeeded"}) is False


def test_preflight_is_blocked_without_live():
    cfg = RuntimeConfig("https://staging.example", "token", "ws", "brand")
    report = run_preflight(cfg, live=False)
    assert report["status"] == "blocked"


def test_preflight_marks_missing_kcpg_and_verification_unobservable(monkeypatch):
    cfg = RuntimeConfig("https://staging.example", "token", "ws", "brand")
    calls = []

    def fake_request(*args, **kwargs):
        calls.append(args[0])
        return {"http_status": 200, "body": {"status": "healthy"}}

    monkeypatch.setattr("scripts.staging_runtime_certification.request_json", fake_request)
    report = run_preflight(cfg, live=True)
    assert report["status"] == "blocked"
    assert "kcpg_health" in report["unobservable"]
    assert "authoritative_execution_evidence" in report["unobservable"]
    assert len(calls) == 2
