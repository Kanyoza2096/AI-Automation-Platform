#!/usr/bin/env python3
"""C9.3 backup/restore certification harness.

The harness is deliberately fail-closed: an offline artifact check can prove
that a backup file is structurally usable, but it cannot prove recovery of a
real KDO database. A LIVE certification requires source and isolated restore
PostgreSQL DSNs plus a deterministic verification query.

No credentials are written to the evidence manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


MAX_DUMP_SECONDS = int(os.getenv("KDO_BACKUP_CERT_TIMEOUT_SECONDS", "900"))


def _redact_dsn(value: str | None) -> str | None:
    if not value:
        return None
    # Keep host/db identity useful without retaining passwords/query secrets.
    if "@" in value and "://" in value:
        scheme, rest = value.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return "<dsn-redacted>"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool(name: str) -> bool:
    try:
        subprocess.run([name, "--version"], check=True, capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def inspect_backup(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"status": "invalid", "reason": "backup file does not exist"}
    size = path.stat().st_size
    if size == 0:
        return {"status": "invalid", "reason": "backup file is empty", "size_bytes": 0}
    digest = sha256_file(path)
    result: dict[str, Any] = {
        "status": "valid",
        "size_bytes": size,
        "sha256": digest,
        "format": "unknown",
    }
    with path.open("rb") as handle:
        magic = handle.read(5)
    if magic == b"PGDMP":
        result["format"] = "custom"
    else:
        head = path.read_bytes()[:256].lower()
        if b"create " in head or b"set " in head or b"--" in head:
            result["format"] = "plain_sql"
        else:
            result["status"] = "invalid"
            result["reason"] = "unrecognized PostgreSQL dump format"
    return result


def _run(command: list[str], timeout: int = MAX_DUMP_SECONDS) -> tuple[int, str, float]:
    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stderr or proc.stdout or "")[-4000:], time.monotonic() - started


def validate_custom_dump(path: Path) -> dict[str, Any]:
    if not _tool("pg_restore"):
        return {"status": "not_proven", "reason": "pg_restore is unavailable"}
    code, output, elapsed = _run(["pg_restore", "--list", str(path)], timeout=60)
    if code != 0:
        return {"status": "failed", "reason": output, "duration_seconds": round(elapsed, 3)}
    entries = [line for line in output.splitlines() if line and not line.startswith(";")]
    return {"status": "passed", "catalog_entries": len(entries), "duration_seconds": round(elapsed, 3)}


def _query_scalar(dsn: str, query: str) -> Any:
    try:
        import psycopg
    except ImportError:
        try:
            import psycopg2 as psycopg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("psycopg/psycopg2 is required for live certification") from exc
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("verification query returned no rows")
            if len(row) != 1:
                raise RuntimeError("verification query must return exactly one column")
            value = row[0]
            # JSON-normalize values commonly returned by Postgres.
            json.dumps(value, default=str)
            return value
    finally:
        conn.close()


def run_live_certification(source_dsn: str, restore_dsn: str, verification_query: str, output: Path) -> dict[str, Any]:
    if not _tool("pg_dump") or not _tool("pg_restore"):
        return {"status": "not_proven", "reason": "pg_dump and pg_restore are required"}
    if not source_dsn or not restore_dsn or not verification_query:
        return {"status": "not_proven", "reason": "source DSN, isolated restore DSN, and verification query are required"}
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    code, stderr, dump_seconds = _run([
        "pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(output), "--dbname", source_dsn,
    ])
    if code != 0:
        return {"status": "failed", "stage": "backup", "error": stderr, "duration_seconds": round(time.monotonic() - started, 3)}

    artifact = inspect_backup(output)
    catalog = validate_custom_dump(output)
    if artifact.get("status") != "valid" or catalog.get("status") != "passed":
        return {"status": "failed", "stage": "artifact_validation", "artifact": artifact, "catalog": catalog}

    source_value = _query_scalar(source_dsn, verification_query)
    code, stderr, restore_seconds = _run([
        "pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", restore_dsn, str(output),
    ])
    if code != 0:
        return {"status": "failed", "stage": "restore", "error": stderr, "artifact": artifact, "duration_seconds": round(time.monotonic() - started, 3)}
    restored_value = _query_scalar(restore_dsn, verification_query)
    if json.dumps(source_value, default=str, sort_keys=True) != json.dumps(restored_value, default=str, sort_keys=True):
        return {
            "status": "failed", "stage": "verification", "reason": "source and restored verification values differ",
            "source_value": source_value, "restored_value": restored_value, "artifact": artifact,
        }
    return {
        "status": "passed",
        "stage": "complete",
        "artifact": artifact,
        "catalog": catalog,
        "source_verification": source_value,
        "restore_verification": restored_value,
        "backup_seconds": round(dump_seconds, 3),
        "restore_seconds": round(restore_seconds, 3),
        "total_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--source-dsn", default=os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL"))
    parser.add_argument("--restore-dsn", default=os.getenv("KDO_RESTORE_DSN"))
    parser.add_argument("--verification-query", default=os.getenv("KDO_BACKUP_VERIFY_QUERY"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--live", action="store_true", help="Require and execute a real backup/restore cycle")
    parser.add_argument("--inspect", action="store_true", help="Inspect an existing backup artifact")
    args = parser.parse_args()

    evidence: dict[str, Any] = {
        "certification": "C9.3",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "not_proven",
        "credentials": {"source_dsn": _redact_dsn(args.source_dsn), "restore_dsn": _redact_dsn(args.restore_dsn)},
        "requirements": {
            "isolated_restore_target": True,
            "deterministic_verification_query": True,
            "backup_checksum": True,
        },
    }

    try:
        if args.inspect:
            if not args.backup_file:
                raise SystemExit("--inspect requires --backup-file")
            artifact = inspect_backup(args.backup_file)
            evidence.update({"artifact": artifact, "status": "passed" if artifact.get("status") == "valid" else "failed"})
            if artifact.get("format") == "custom":
                evidence["catalog"] = validate_custom_dump(args.backup_file)
        elif args.live:
            if not args.backup_file:
                fd, name = tempfile.mkstemp(prefix="kdo-c9.3-", suffix=".dump")
                os.close(fd)
                args.backup_file = Path(name)
                evidence["temporary_backup"] = True
            evidence.update(run_live_certification(args.source_dsn, args.restore_dsn, args.verification_query, args.backup_file))
        else:
            evidence["reason"] = "no live certification requested; use --live with an isolated restore database"
    except Exception as exc:
        evidence.update({"status": "failed", "reason": str(exc)[:1000]})

    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")

    print(json.dumps(evidence, indent=2, default=str))
    if evidence["status"] == "passed":
        return 0
    if evidence["status"] == "not_proven":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
