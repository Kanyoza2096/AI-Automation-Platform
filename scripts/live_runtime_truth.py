#!/usr/bin/env python3
"""Read-only live runtime truth probe for KCPG -> Python -> Director.

Required env:
  KDO_RUNTIME_BASE_URL  Gateway base URL, e.g. https://gateway.example.com
  KDO_RUNTIME_TOKEN     Existing operator JWT/service credential with read access
Optional:
  KDO_RUNTIME_WORKSPACE_ID  Workspace header

The probe never prints the supplied credential. It distinguishes HTTP auth,
route, upstream, and service-health failures instead of collapsing them.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DIRECTOR_READS = [
    "/director/health", "/director/status", "/director/briefing", "/director/tasks",
    "/director/approvals", "/director/engines", "/director/diagnostics",
    "/director/diagnostics/root-cause", "/director/diagnostics/cascading",
    "/director/diagnostics/impact", "/director/diagnostics/recovery-plans",
    "/director/decisions?limit=5", "/director/alerts?limit=5", "/director/capabilities",
    "/director/outlook", "/director/allocation", "/director/summary",
    "/director/initiatives", "/director/correlations", "/director/daily-briefing",
    "/director/learning/report", "/director/learning/contexts",
    "/director/learning/time-patterns", "/director/learning/failures?limit=5",
    "/director/learning/slowest?limit=5", "/director/ooda/state",
    "/director/ooda/world-model", "/director/ooda/priorities",
    "/director/ooda/action-preferences", "/director/ooda/causal-insights",
    "/director/ooda/learner-report", "/director/ooda/intelligence-report",
    "/director/meta/status", "/director/meta/impact", "/director/meta/topology",
]


def get(path: str, token: str, workspace: str) -> tuple[int, Any]:
    url = os.environ["KDO_RUNTIME_BASE_URL"].rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw[:500]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw[:500]
        return exc.code, body
    except Exception as exc:
        return 0, {"error": type(exc).__name__, "message": str(exc)[:300]}


def classify(status: int) -> str:
    if 200 <= status < 300: return "OK"
    if status in (401, 403): return "AUTH"
    if status == 404: return "ROUTE"
    if status >= 500: return "UPSTREAM"
    if status == 0: return "NETWORK"
    return "OTHER"


def main() -> int:
    base = os.getenv("KDO_RUNTIME_BASE_URL", "").strip()
    token = os.getenv("KDO_RUNTIME_TOKEN", "").strip()
    workspace = os.getenv("KDO_RUNTIME_WORKSPACE_ID", "").strip()
    if not base or not token:
        print("BLOCKED: set KDO_RUNTIME_BASE_URL and KDO_RUNTIME_TOKEN")
        return 2

    print("KDO LIVE RUNTIME TRUTH")
    print(f"base={base}")
    if workspace:
        print(f"workspace={workspace}")

    status, body = get("/api/v1/system/health", token, workspace)
    print(f"SYSTEM_HEALTH {status} {classify(status)}")
    if isinstance(body, dict):
        print(json.dumps({
            "status": body.get("status"),
            "services": body.get("services", {}),
            "connectors": body.get("connectors", {}),
        }, indent=2, sort_keys=True, default=str))
    else:
        print(str(body))

    counts = {"OK": 0, "AUTH": 0, "ROUTE": 0, "UPSTREAM": 0, "NETWORK": 0, "OTHER": 0}
    for path in DIRECTOR_READS:
        status, body = get(path, token, workspace)
        kind = classify(status)
        counts[kind] += 1
        detail = ""
        if isinstance(body, dict):
            err = body.get("detail") or body.get("error")
            if err: detail = f" detail={str(err)[:160]}"
        print(f"DIRECTOR {status:>3} {kind:<8} {path}{detail}")

    print("SUMMARY " + json.dumps(counts, sort_keys=True))
    return 0 if counts["AUTH"] == 0 and counts["NETWORK"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
