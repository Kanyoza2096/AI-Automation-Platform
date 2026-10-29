#!/usr/bin/env python3
"""Static production-release gate for KDO.

This gate intentionally does not pretend to certify external infrastructure.
It validates the release artifact itself and reports which checks require a
real staging environment (database, Redis, provider APIs, Docker runtime).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platform_version import PLATFORM_VERSION
VERSION = PLATFORM_VERSION

REQUIRED = [
    "main.py", "requirements.txt", "Dockerfile", ".dockerignore",
    "deploy/docker-compose.production.yml", "scripts/verify_migrations.py",
    "migrations/001_initial_schema.sql", "migrations/030_production_tenant_security.sql",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def check_files() -> None:
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            fail(f"missing required release file: {rel}")
    print(f"OK: required release files ({len(REQUIRED)})")


def check_versions() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    version_module = (ROOT / "platform_version.py").read_text(encoding="utf-8")
    if f'PLATFORM_VERSION = "{VERSION}"' not in version_module:
        fail("platform_version.py does not define the configured release version")
    if "from platform_version import PLATFORM_VERSION" not in main:
        fail("main.py does not consume the canonical platform version")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "COPY . ." not in dockerfile:
        fail("Dockerfile does not package the application source tree")
    print(f"OK: release version {VERSION}")
    print(f"OK: release version {VERSION}")


def check_migrations() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/verify_migrations.py", "--structure-only"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode:
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        fail("migration structure check failed")
    print(proc.stdout.strip())


def check_compile() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "."],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode:
        fail("Python compilation failed")
    print("OK: Python compilation")


def check_tests() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT, text=True,
    )
    if proc.returncode:
        fail("test suite failed")
    print("OK: test suite")


def check_docker_safety() -> None:
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env*", "tests/"):
        if pattern not in ignore:
            fail(f".dockerignore missing {pattern}")
    print("OK: Docker build context excludes secrets/tests")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()
    check_files()
    check_versions()
    check_migrations()
    check_compile()
    check_docker_safety()
    if not args.skip_tests:
        check_tests()
    print("PRODUCTION ARTIFACT GATE: PASS")
    print("STAGING REQUIRED: real DB/RLS, Redis, provider APIs, Docker build/runtime")


if __name__ == "__main__":
    main()
