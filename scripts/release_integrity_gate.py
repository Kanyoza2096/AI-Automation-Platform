#!/usr/bin/env python3
"""C9 production release-integrity gate.

Checks the deployable artifact itself. It deliberately does not certify
external infrastructure or provider behavior.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def check_required_files() -> None:
    required = (
        "main.py",
        "platform_version.py",
        "requirements.txt",
        "Dockerfile",
        ".dockerignore",
        "deploy/docker-compose.production.yml",
        "scripts/production_gate.py",
        "scripts/production_certification_gate.py",
        "scripts/security_surface_check.py",
        "scripts/worker_health.py",
        "scripts/backup_restore_certification.py",
        "C9.3_BACKUP_RESTORE_CERTIFICATION.md",
        "C9.5_PRODUCTION_READINESS_GATE.md",
        "scripts/c9_production_readiness_gate.py",
    )
    for rel in required:
        if not (ROOT / rel).is_file():
            fail(f"missing release file: {rel}")


def check_identity() -> None:
    version_source = (ROOT / "platform_version.py").read_text(encoding="utf-8")
    match = re.search(r'^PLATFORM_VERSION\s*=\s*["\']([^"\']+)["\']\s*$', version_source, re.M)
    if not match:
        fail("canonical PLATFORM_VERSION is missing")
        return
    version = match.group(1)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"release version is not semantic-version shaped: {version}")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    if "from platform_version import PLATFORM_VERSION" not in main:
        fail("main.py does not consume canonical release identity")


def check_container() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for needle in ("USER botuser", "HEALTHCHECK", "COPY requirements.txt .", "COPY . ."):
        if needle not in dockerfile:
            fail(f"Dockerfile missing production control: {needle}")
    compose = (ROOT / "deploy/docker-compose.production.yml").read_text(encoding="utf-8")
    for needle in (
        "SERVICE_MODE: api",
        "SERVICE_MODE: core",
        "SERVICE_MODE: workers",
        "gunicorn main:app",
        "/health/live",
        "restart: unless-stopped",
        "test: [\"CMD\", \"python\", \"scripts/worker_health.py\"]",
        "KDO_HEARTBEAT_FILE",
    ):
        if needle not in compose:
            fail(f"production compose missing: {needle}")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for needle in (".env*", "tests/", "__pycache__/"):
        if needle not in ignore:
            fail(f".dockerignore missing: {needle}")


def check_for_release_debris() -> None:
    forbidden_suffixes = {".bak", ".backup", ".orig"}
    debris = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in forbidden_suffixes
    ]
    if debris:
        fail("release artifact contains backup/debris files: " + ", ".join(sorted(debris)[:20]))
    pycache = [p for p in ROOT.rglob("__pycache__") if p.is_dir()]
    if pycache:
        warn("__pycache__ directories exist in the working tree; .dockerignore excludes them")


def check_migrations() -> None:
    migration_dir = ROOT / "migrations"
    entries = []
    for path in sorted(migration_dir.glob("*.sql"), key=lambda p: (int(re.match(r"^(\d+)_", p.name).group(1)) if re.match(r"^(\d+)_", p.name) else 10**9, p.name)):
        m = re.match(r"^(\d+)_", path.name)
        if not m:
            fail(f"unnumbered migration: {path.name}")
            continue
        entries.append((int(m.group(1)), path.name))
    duplicates = sorted({n for n, _ in entries if [x[0] for x in entries].count(n) > 1})
    if duplicates:
        warn("legacy duplicate migration prefixes remain authoritative by filename order: " + ", ".join(map(str, duplicates)))


def main() -> int:
    check_required_files()
    check_identity()
    check_container()
    check_for_release_debris()
    check_migrations()
    print("C9 RELEASE INTEGRITY GATE")
    for message in warnings:
        print("WARNING:", message)
    for message in failures:
        print("FAIL:", message)
    if failures:
        print(f"GATE: BLOCKED ({len(failures)} failures)")
        return 1
    print("GATE: ARTIFACT INTEGRITY PASS")
    print("NOTE: external DB/RLS, Redis, provider, Docker runtime, and performance evidence remain staging gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
