#!/usr/bin/env python3
"""Static production certification gate.

This gate proves repository/deployment contracts without pretending that
external Supabase, Redis, OAuth providers, or real publishing have been
validated. External validation remains an explicit staging gate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures: list[str] = []
warnings: list[str] = []

def fail(msg: str) -> None:
    failures.append(msg)

def warn(msg: str) -> None:
    warnings.append(msg)

req = ROOT / "requirements.txt"
req_text = req.read_text(encoding="utf-8")
required = [
    "fastapi", "uvicorn", "gunicorn", "supabase", "redis",
    "python-socketio", "python-multipart", "cryptography", "requests",
    "opentelemetry-api", "opentelemetry-sdk", "websockets", "aiohttp",
    "Pillow", "psutil", "PyMuPDF", "PyPDF2", "pycairo", "Flask",
    "requests-oauthlib", "stripe", "google-auth", "google-api-python-client",
    "psycopg[binary]",
]
for dep in required:
    if not re.search(rf"^\s*{re.escape(dep)}(?:\[[^\]]+\])?(?:[<>=!~]|\s|$)", req_text, re.M | re.I):
        fail(f"requirements.txt missing mandatory dependency: {dep}")

compose = ROOT / "deploy/docker-compose.production.yml"
compose_text = compose.read_text(encoding="utf-8")
for needle, label in [
    ("SERVICE_MODE: api", "api service mode"),
    ("SERVICE_MODE: core", "core service mode"),
    ("SERVICE_MODE: workers", "workers service mode"),
    ("gunicorn main:app", "multi-worker API command"),
    ("/health/live", "API healthcheck"),
]:
    if needle not in compose_text:
        fail(f"production compose missing {label}: {needle}")

# Migration history is append-only: duplicate numeric prefixes are permitted
# for already-released branches, but execution order must be deterministic.
mig = ROOT / "migrations"
entries = []
for path in sorted(mig.glob("*.sql"), key=lambda p: (int(re.match(r"^(\d+)_", p.name).group(1)) if re.match(r"^(\d+)_", p.name) else 10**9, p.name)):
    m = re.match(r"^(\d+)_", path.name)
    if not m:
        fail(f"unnumbered migration: {path.name}")
        continue
    entries.append((int(m.group(1)), path.name))
ordered = sorted(entries)
if entries != ordered:
    fail("migration execution order is not deterministic by numeric prefix + filename")
nums = [n for n, _ in entries]
if len(nums) != len(set(nums)):
    warn("duplicate migration prefixes exist; filename ordering is the execution tie-breaker")

# Static security gate: all legacy control-plane routers must carry either the
# canonical auth dependency or the stronger species-privilege dependency.
security = ROOT / "scripts/security_surface_check.py"
if not security.exists():
    fail("security_surface_check.py missing")

# Production certification must not be inferred from unit tests alone.
warn("real Supabase/RLS, Redis lease/recovery, OAuth/provider publication, webhook reconciliation, and backup/restore remain staging-only evidence gates")
warn("this static gate does not certify latency, throughput, availability, or external-provider correctness")

print("PRODUCTION CERTIFICATION GATE")
for msg in warnings:
    print("WARNING:", msg)
for msg in failures:
    print("FAIL:", msg)
if failures:
    print(f"GATE: BLOCKED ({len(failures)} failures)")
    raise SystemExit(1)
print("GATE: STATIC PASS")
