#!/usr/bin/env python3
"""Controlled staging runtime certification gate.

This runner separates three things that are often incorrectly conflated:
1. service reachability,
2. authoritative execution evidence, and
3. real provider publication.

By default it performs no mutation. ``--live`` permits authenticated staging
reads. ``--publish`` additionally invokes the existing phase-37.9 controlled
publish runner and therefore requires ``--live``.

Required for live preflight:
  KANYOZA_STAGING_BASE_URL
  KANYOZA_STAGING_TOKEN
  KANYOZA_STAGING_WORKSPACE_ID
  KANYOZA_STAGING_BRAND_ID

Optional:
  KCPG_STAGING_BASE_URL
  KCPG_STAGING_TOKEN
  KANYOZA_STAGING_VERIFY_URL
  KANYOZA_STAGING_TIMEOUT

A verification endpoint must return authoritative durable/provider evidence if
it is used to claim provider publication. HTTP 200 alone is not publication
proof; the returned payload must contain explicit terminal execution/provider
fields.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    token: str
    workspace_id: str
    brand_id: str
    kcpg_base_url: str | None = None
    kcpg_token: str | None = None
    verify_url: str | None = None
    timeout: float = 10.0


def load_config() -> RuntimeConfig:
    required = {
        "base_url": os.getenv("KANYOZA_STAGING_BASE_URL", "").strip().rstrip("/"),
        "token": os.getenv("KANYOZA_STAGING_TOKEN", "").strip(),
        "workspace_id": os.getenv("KANYOZA_STAGING_WORKSPACE_ID", "").strip(),
        "brand_id": os.getenv("KANYOZA_STAGING_BRAND_ID", "").strip(),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise SystemExit("Missing required staging variables: " + ", ".join(missing))
    try:
        timeout = max(1.0, float(os.getenv("KANYOZA_STAGING_TIMEOUT", "10")))
    except ValueError as exc:
        raise SystemExit("KANYOZA_STAGING_TIMEOUT must be numeric") from exc
    return RuntimeConfig(
        **required,
        kcpg_base_url=os.getenv("KCPG_STAGING_BASE_URL", "").strip().rstrip("/") or None,
        kcpg_token=os.getenv("KCPG_STAGING_TOKEN", "").strip() or None,
        verify_url=os.getenv("KANYOZA_STAGING_VERIFY_URL", "").strip() or None,
        timeout=timeout,
    )


def request_json(cfg: RuntimeConfig, url: str, token: str | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}" if token else "",
            "Accept": "application/json",
            "X-Kanyoza-Staging-Certification": "phase-89",
            "X-Kanyoza-Workspace": cfg.workspace_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw[:2000]}
            return {"http_status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw[:2000]}
        return {"http_status": exc.code, "body": body}
    except Exception as exc:
        return {"http_status": 0, "body": {"error": f"{type(exc).__name__}: {exc}"}}


def probe(name: str, response: dict[str, Any], *, acceptable: set[int] = {200}) -> dict[str, Any]:
    status = int(response.get("http_status") or 0)
    if status not in acceptable:
        return {"name": name, "status": "fail", "detail": f"HTTP {status}", "response": response.get("body")}
    return {"name": name, "status": "pass", "detail": f"HTTP {status}"}


def evidence_is_authoritative(body: Any) -> bool:
    """Require explicit durable/provider terminal evidence, never mere HTTP success."""
    if not isinstance(body, dict):
        return False
    candidates = [body]
    for key in ("verification", "evidence", "execution", "result"):
        value = body.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    terminal = {"succeeded", "completed", "published", "failed", "reconciliation_required"}
    for item in candidates:
        status = str(item.get("status", "")).lower()
        provider_object = item.get("provider_object_id") or item.get("provider_object") or item.get("external_id")
        if status in terminal and provider_object:
            return True
    return False


def run_preflight(cfg: RuntimeConfig, *, live: bool) -> dict[str, Any]:
    correlation_id = f"stg89_{uuid4().hex[:12]}"
    report: dict[str, Any] = {"correlation_id": correlation_id, "environment": "staging", "probes": []}
    if not live:
        report["status"] = "blocked"
        report["reason"] = "--live is required for authenticated staging reads"
        return report

    backend_health = request_json(cfg, cfg.base_url + "/api/v1/health/live", cfg.token)
    report["probes"].append(probe("backend_health", backend_health))

    system_health = request_json(cfg, cfg.base_url + "/api/v1/system/health", cfg.token)
    report["probes"].append(probe("backend_system_health", system_health))

    if cfg.kcpg_base_url and cfg.kcpg_token:
        kcpg_health = request_json(cfg, cfg.kcpg_base_url + "/health", cfg.kcpg_token)
        report["probes"].append(probe("kcpg_health", kcpg_health))
    else:
        report["probes"].append({"name": "kcpg_health", "status": "unobservable", "detail": "KCPG staging URL/token not configured"})

    if cfg.verify_url:
        verification = request_json(cfg, cfg.verify_url, cfg.token)
        item = probe("authoritative_execution_evidence", verification)
        if item["status"] == "pass" and not evidence_is_authoritative(verification.get("body")):
            item = {"name": "authoritative_execution_evidence", "status": "unobservable", "detail": "verification endpoint returned no explicit terminal provider evidence"}
        report["probes"].append(item)
    else:
        report["probes"].append({"name": "authoritative_execution_evidence", "status": "unobservable", "detail": "KANYOZA_STAGING_VERIFY_URL not configured"})

    failures = [p["name"] for p in report["probes"] if p["status"] == "fail"]
    unknown = [p["name"] for p in report["probes"] if p["status"] == "unobservable"]
    report["failures"] = failures
    report["unobservable"] = unknown
    report["promotable"] = not failures and not unknown
    report["status"] = "pass" if report["promotable"] else "blocked"
    return report


def run_publish(cfg: RuntimeConfig, *, prefix: str, wait_seconds: int) -> dict[str, Any]:
    script = os.path.join(os.path.dirname(__file__), "staging_e2e_publish.py")
    command = [sys.executable, script, "--live", "--prefix", prefix, "--wait-seconds", str(max(0, wait_seconds))]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        body = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        body = {"raw": completed.stdout[-4000:]}
    return {"exit_code": completed.returncode, "report": body, "stderr": completed.stderr[-2000:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow authenticated staging reads")
    parser.add_argument("--publish", action="store_true", help="also run controlled real staging publish certification")
    parser.add_argument("--prefix", default="Kanyoza")
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.publish and not args.live:
        print(json.dumps({"status": "blocked", "reason": "--publish requires --live"}, indent=2))
        return 2
    try:
        cfg = load_config()
        report = run_preflight(cfg, live=args.live)
        if args.publish and report.get("promotable"):
            report["publish"] = run_publish(cfg, prefix=args.prefix, wait_seconds=args.wait_seconds)
            publish_report = report["publish"].get("report") or {}
            verification = publish_report.get("verification") or {}
            report["provider_publication_proven"] = evidence_is_authoritative(verification.get("body"))
            if not report["provider_publication_proven"]:
                report["promotable"] = False
                report["status"] = "blocked"
                report.setdefault("blockers", []).append("provider_publication_not_proven")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("status") == "pass" else 2
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
