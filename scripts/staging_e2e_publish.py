#!/usr/bin/env python3
"""Controlled real-staging publish certification runner.

This script never invents infrastructure success. It requires explicit staging
configuration and --live before it can issue a real publish mutation.

It exercises the public trigger surfaces:
  1. immediate/manual POST /api/v1/posts
  2. scheduled POST /api/v1/posts
  3. automated trend.publish.requested POST /api/events/publish

The API response proves acceptance/queueing only. Publication proof must be
supplied by the staging verification endpoint configured through
KANYOZA_STAGING_VERIFY_URL (or inspected in the durable execution/provider
records). This avoids confusing "queued" with "published".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str
    workspace_id: str
    brand_id: str
    verify_url: str | None = None
    timeout: float = 20.0


def load_config() -> Config:
    values = {
        "base_url": os.getenv("KANYOZA_STAGING_BASE_URL", "").strip().rstrip("/"),
        "token": os.getenv("KANYOZA_STAGING_TOKEN", "").strip(),
        "workspace_id": os.getenv("KANYOZA_STAGING_WORKSPACE_ID", "").strip(),
        "brand_id": os.getenv("KANYOZA_STAGING_BRAND_ID", "").strip(),
        "verify_url": os.getenv("KANYOZA_STAGING_VERIFY_URL", "").strip() or None,
    }
    missing = [k for k in ("base_url", "token", "workspace_id", "brand_id") if not values[k]]
    if missing:
        raise SystemExit("Missing required staging variables: " + ", ".join(missing))
    return Config(**values)


def request_json(cfg: Config, path_or_url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else cfg.base_url + path_or_url
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {cfg.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Kanyoza-Staging-Certification": "phase-37.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            raw = response.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
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


def require_success(label: str, response: dict[str, Any], accepted_statuses: set[int]) -> dict[str, Any]:
    status = int(response.get("http_status") or 0)
    if status not in accepted_statuses:
        raise RuntimeError(f"{label} failed with HTTP {status}: {response.get('body')}")
    body = response.get("body") or {}
    if isinstance(body, dict) and body.get("ok") is False:
        raise RuntimeError(f"{label} rejected: {body}")
    return body


def run(cfg: Config, *, live: bool, prefix: str, wait_seconds: int) -> dict[str, Any]:
    if not live:
        return {
            "status": "blocked",
            "reason": "--live is required before any real external publish mutation",
            "required": ["KANYOZA_STAGING_BASE_URL", "KANYOZA_STAGING_TOKEN", "KANYOZA_STAGING_WORKSPACE_ID", "KANYOZA_STAGING_BRAND_ID"],
        }

    run_id = f"stg37_9_{uuid.uuid4().hex[:12]}"
    caption = f"{prefix} manual certification {run_id}"
    results: dict[str, Any] = {"run_id": run_id, "tests": []}

    manual = request_json(cfg, "/api/v1/posts", {
        "workspace_id": cfg.workspace_id,
        "brand_id": cfg.brand_id,
        "platforms": ["facebook"],
        "topic": "Kanyoza staging certification",
        "caption": caption,
        "state": "publish",
        "risk_level": "low",
        "idempotency_key": f"{run_id}:manual",
    })
    manual_body = require_success("manual publish trigger", manual, {200, 201, 202})
    results["tests"].append({"name": "manual", "accepted": True, "response": manual_body})

    scheduled_for = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    scheduled = request_json(cfg, "/api/v1/posts", {
        "workspace_id": cfg.workspace_id,
        "brand_id": cfg.brand_id,
        "platforms": ["facebook"],
        "topic": "Kanyoza scheduled certification",
        "caption": f"{prefix} scheduled certification {run_id}",
        "state": "publish",
        "scheduled_for": scheduled_for,
        "risk_level": "low",
        "idempotency_key": f"{run_id}:scheduled",
    })
    scheduled_body = require_success("scheduled publish trigger", scheduled, {200, 201, 202})
    results["tests"].append({"name": "scheduled", "accepted": True, "response": scheduled_body})

    automated = request_json(cfg, "/api/events/publish", {
        "event_name": "trend.publish.requested",
        "event_version": "1.0",
        "payload": {
            "workspace_id": cfg.workspace_id,
            "brand_id": cfg.brand_id,
            "platform": "facebook",
            "job_id": f"{run_id}:automated",
            "topic": "Kanyoza automated certification",
            "content": f"{prefix} automated certification {run_id}",
            "decision": "publish",
        },
    })
    automated_body = require_success("automated publish trigger", automated, {200, 201, 202})
    results["tests"].append({"name": "automated", "accepted": True, "response": automated_body})

    if cfg.verify_url:
        if wait_seconds:
            time.sleep(wait_seconds)
        verification = request_json(cfg, cfg.verify_url, None)
        results["verification"] = verification
        if int(verification.get("http_status") or 0) != 200:
            raise RuntimeError(f"staging verification endpoint failed: {verification}")
    else:
        results["verification"] = {
            "status": "unobservable",
            "reason": "KANYOZA_STAGING_VERIFY_URL not configured; trigger acceptance is not proof of Facebook publication",
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow real staging publish mutations")
    parser.add_argument("--prefix", default="Kanyoza")
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    try:
        cfg = load_config()
        report = run(cfg, live=args.live, prefix=args.prefix, wait_seconds=max(0, args.wait_seconds))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("status") != "blocked" else 2
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
