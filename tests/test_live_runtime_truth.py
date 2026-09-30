from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_runtime_truth_probe_is_read_only_and_never_prints_token():
    text = (ROOT / "scripts" / "live_runtime_truth.py").read_text()
    assert 'method="GET"' in text
    assert "print(token)" not in text
    assert "KDO_RUNTIME_TOKEN" in text
    assert "/api/v1/system/health" in text
    assert "/director/health" in text
    assert "/director/status" in text
