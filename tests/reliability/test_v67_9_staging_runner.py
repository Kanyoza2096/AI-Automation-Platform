from __future__ import annotations

from scripts.staging_v67_9_chaos import run_live, main


def test_v67_9_is_fail_closed_without_live_environment(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    report = run_live(workspace_id="ws")
    assert report.status == "blocked"
    assert report.error


def test_v67_9_cli_blocks_without_explicit_live(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert main([]) == 2
    assert '"status": "blocked"' in capsys.readouterr().out
