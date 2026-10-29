#!/usr/bin/env python3
"""Whole-system Render live certification harness.

Read-only by default. It never treats HTTP acceptance as external success.

Required for --live:
  KDO_RUNTIME_BASE_URL
  KDO_RUNTIME_TOKEN
Optional:
  KDO_RUNTIME_WORKSPACE_ID

With --exercise, also runs the existing synthetic customer.created smoke test
when its webhook credentials are configured. This is only a controlled sample
of the external execution plane; it does not claim every provider is live.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

READ_PATHS = [
    "/api/v1/system/health",
    "/health",
    "/api/v1/runtime/route-event-wiring",
    "/director/health",
    "/director/status",
    "/director/briefing",
    "/director/engines",
    "/director/diagnostics",
    "/director/approvals",
    "/director/capabilities",
    "/director/decisions?limit=5",
    "/director/alerts?limit=5",
    "/director/outlook",
    "/director/summary",
    "/director/initiatives",
    "/director/correlations",
    "/director/daily-briefing",
    "/director/learning/report",
    "/director/learning/contexts",
    "/director/learning/failures?limit=5",
    "/director/ooda/state",
    "/director/ooda/world-model",
    "/director/ooda/priorities",
    "/director/ooda/action-preferences",
    "/director/ooda/causal-insights",
    "/director/ooda/learner-report",
    "/director/ooda/intelligence-report",
    "/director/meta/status",
    "/director/meta/impact",
    "/director/meta/topology",
]

LOCAL_GATES = [
    [sys.executable, "scripts/production_certification_gate.py"],
    [sys.executable, "scripts/security_surface_check.py"],
    [sys.executable, "-m", "pytest", "-q",
     "tests/test_production_certification_gate.py",
     "tests/test_v72_0_production_execution_certification.py",
     "tests/test_action_execution_kernel.py",
     "tests/test_staging_validation.py",
     "tests/test_staging_evidence.py",
     "tests/test_ai_provider_fabric.py"],
]

def request(base: str, path: str, token: str, workspace: str):
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "replace")
            try: body = json.loads(raw)
            except Exception: body = raw[:500]
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]
    except Exception as e:
        return 0, {"error": type(e).__name__, "message": str(e)[:250]}


def post_json(base: str, path: str, token: str, workspace: str, payload: dict):
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="POST", data=json.dumps(payload).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode("utf-8", "replace")
            try: body = json.loads(raw)
            except Exception: body = raw[:1000]
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:1000]
    except Exception as e:
        return 0, {"error": type(e).__name__, "message": str(e)[:250]}

def classify(code: int) -> str:
    if 200 <= code < 300: return "OK"
    if code in (401, 403): return "AUTH"
    if code == 404: return "ROUTE"
    if code >= 500: return "UPSTREAM"
    if code == 0: return "NETWORK"
    return "OTHER"

def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--exercise", action="store_true", help="run the controlled onboarding smoke too")
    args = ap.parse_args()
    if not args.live:
        print(json.dumps({"status":"blocked", "reason":"pass --live to permit Render network calls"}))
        return 2

    base = os.getenv("KDO_RUNTIME_BASE_URL", "").strip()
    token = os.getenv("KDO_RUNTIME_TOKEN", "").strip()
    workspace = os.getenv("KDO_RUNTIME_WORKSPACE_ID", "").strip()
    if not base or not token:
        print(json.dumps({"status":"blocked", "reason":"missing KDO_RUNTIME_BASE_URL or KDO_RUNTIME_TOKEN"}))
        return 2

    print("=== KDO WHOLE-SYSTEM RENDER CERTIFICATION ===")
    print(f"base={base}")
    counts = {k: 0 for k in ("OK","AUTH","ROUTE","UPSTREAM","NETWORK","OTHER")}
    failed = []
    for path in READ_PATHS:
        code, body = request(base, path, token, workspace)
        kind = classify(code); counts[kind] += 1
        print(f"LIVE {code:>3} {kind:<8} {path}")
        if kind in ("AUTH","UPSTREAM","NETWORK"):
            failed.append((path, kind))

    print("LIVE SUMMARY", json.dumps(counts, sort_keys=True))

    ai_exercise = None
    if args.exercise and os.getenv("KDO_RUN_LIVE_AI", "0") == "1":
        print("=== LIVE AI PROVIDER CERTIFICATION ===")
        ai_code, ai_body = post_json(
            base, "/api/v1/ai/interpret", token, workspace,
            {
                "task": "interpretation",
                "message": "A customer reports that order ORD-CERT-001 is delayed. Classify the issue and return a JSON interpretation with intent, priority, and recommended_actions. Do not execute anything.",
                "structured": True,
            },
        )
        ai_kind = classify(ai_code)
        receipt = ai_body.get("ai_receipt", {}) if isinstance(ai_body, dict) else {}
        ai_exercise = {
            "http_status": ai_code,
            "classification": ai_kind,
            "receipt_status": receipt.get("status"),
            "provider": receipt.get("provider"),
            "model": receipt.get("model"),
            "has_raw_output": bool(receipt.get("raw_output")),
            "action_intent_created": bool(ai_body.get("action_intent_created")) if isinstance(ai_body, dict) else None,
        }
        print("LIVE AI", json.dumps(ai_exercise, sort_keys=True))
        if ai_code != 200 or receipt.get("status") != "succeeded" or not receipt.get("raw_output") or ai_body.get("action_intent_created") is not False:
            failed.append(("/api/v1/ai/interpret", "AI_CONTRACT"))
    elif args.exercise:
        print("LIVE_AI=NOT_RUN (set KDO_RUN_LIVE_AI=1 to invoke the configured Cloudflare AI provider)")

    print("=== LOCAL/RELEASE GATES ===")
    local_failures = 0
    for cmd in LOCAL_GATES:
        rc = run(cmd)
        local_failures += int(rc != 0)

    if args.exercise:
        print("=== CONTROLLED EXTERNAL EXECUTION EXERCISE ===")
        rc = run([sys.executable, "scripts/render_customer_onboarding_smoke.py", "--live"])
        local_failures += int(rc != 0)
        if os.getenv("KDO_RUN_LIVE_ONBOARDING", "0") == "1":
            rc = run([sys.executable, "scripts/render_customer_onboarding_smoke.py", "--live", "--publish"])
            local_failures += int(rc != 0)
        else:
            print("LIVE_ONBOARDING=NOT_RUN (set KDO_RUN_LIVE_ONBOARDING=1 for the controlled external-action exercise)")

    result = {
        "status": "pass" if not failed and local_failures == 0 else "blocked_or_failed",
        "live_counts": counts,
        "live_failures": failed,
        "local_gate_failures": local_failures,
        "external_provider_certification": "not_claimed",
        "live_ai_exercise": ai_exercise,
    }
    print("CERTIFICATION RESULT")
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
