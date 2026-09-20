from pathlib import Path

from scripts import backup_restore_certification as cert


def test_inspect_custom_dump(tmp_path):
    path = tmp_path / "kdo.dump"
    path.write_bytes(b"PGDMP" + b"x" * 2048)
    result = cert.inspect_backup(path)
    assert result["status"] == "valid"
    assert result["format"] == "custom"
    assert len(result["sha256"]) == 64


def test_inspect_rejects_empty_artifact(tmp_path):
    path = tmp_path / "empty.dump"
    path.write_bytes(b"")
    result = cert.inspect_backup(path)
    assert result["status"] == "invalid"


def test_inspect_rejects_unknown_artifact(tmp_path):
    path = tmp_path / "bad.dump"
    path.write_bytes(b"not a postgres dump")
    result = cert.inspect_backup(path)
    assert result["status"] == "invalid"


def test_missing_pg_restore_is_not_proven(monkeypatch, tmp_path):
    path = tmp_path / "kdo.dump"
    path.write_bytes(b"PGDMP" + b"x" * 2048)
    monkeypatch.setattr(cert, "_tool", lambda name: False)
    result = cert.validate_custom_dump(path)
    assert result["status"] == "not_proven"


def test_live_requires_explicit_isolated_restore_and_query(monkeypatch, tmp_path):
    monkeypatch.setattr(cert, "_tool", lambda name: True)
    result = cert.run_live_certification("postgresql://source", "", "", tmp_path / "kdo.dump")
    assert result["status"] == "not_proven"


def test_dsn_is_redacted():
    assert cert._redact_dsn("postgresql://alice:secret@example/db") == "postgresql://alice:***@example/db"
