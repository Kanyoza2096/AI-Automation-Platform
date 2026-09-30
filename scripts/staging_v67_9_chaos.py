"""V67.9 real-staging ownership/lease certification runner.

This runner exercises the actual Supabase RPCs against a staging database. It
never invokes a business provider. The probe action is an intentionally
unsupported action type and therefore can only validate durable claim,
renewal, fencing and expiry at the control-plane boundary.

Required environment when --live is used:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  KANYOZA_STAGING_WORKSPACE_ID

The runner is fail-closed: without --live it performs no network operation.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from urllib import request, error


@dataclass
class Report:
    status: str
    checks: dict
    intent_id: str = ""
    error: str = ""


class SupabaseRPC:
    def __init__(self, base_url: str, service_key: str):
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key

    def rpc(self, fn: str, args: dict):
        body = json.dumps(args).encode()
        req = request.Request(
            f"{self.base_url}/rest/v1/rpc/{fn}",
            data=body,
            method="POST",
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"RPC {fn} HTTP {exc.code}: {detail[:1000]}") from exc

    def insert_probe(self, row: dict):
        body = json.dumps(row).encode()
        req = request.Request(
            f"{self.base_url}/rest/v1/action_outbox",
            data=body,
            method="POST",
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as resp:
                resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"probe insert HTTP {exc.code}: {detail[:1000]}") from exc

    def delete_probe(self, intent_id: str):
        req = request.Request(
            f"{self.base_url}/rest/v1/action_outbox?intent_id=eq.{intent_id}",
            method="DELETE",
            headers={"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"},
        )
        try:
            with request.urlopen(req, timeout=20) as resp:
                resp.read()
        except error.HTTPError:
            pass


def run_live(*, workspace_id: str, wait_seconds: int = 31) -> Report:
    base = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key or not workspace_id:
        return Report("blocked", {}, error="SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and KANYOZA_STAGING_WORKSPACE_ID are required")

    db = SupabaseRPC(base, key)
    intent_id = "chaos_v679_" + uuid.uuid4().hex[:20]
    idempotency = "chaos-v679-" + uuid.uuid4().hex
    checks = {}
    row = {
        "intent_id": intent_id,
        "workspace_id": workspace_id,
        "action_type": "__kdo_v67_9_control_plane_probe__",
        "parameters": {"purpose": "lease-certification", "external_side_effect": False},
        "risk_level": "low",
        "idempotency_key": idempotency,
        "source": "v67.9-staging-certification",
        "status": "pending",
    }
    worker_a = "v67.9-a-" + uuid.uuid4().hex[:10]
    worker_b = "v67.9-b-" + uuid.uuid4().hex[:10]
    claimed = None
    try:
        db.insert_probe(row)
        claimed_rows = db.rpc("claim_action_outbox", {"p_limit": 1, "p_lease_seconds": 30, "p_worker_id": worker_a}) or []
        if not claimed_rows:
            return Report("failed", {"claim": False}, intent_id, "worker A could not claim probe")
        claimed = claimed_rows[0]
        checks["claim"] = claimed.get("lease_owner") == worker_a and claimed.get("status") == "processing"
        checks["live_lease_blocks_competitor"] = not bool(db.rpc("claim_action_outbox", {"p_limit": 1, "p_lease_seconds": 30, "p_worker_id": worker_b}) or [])
        checks["current_token_renews"] = bool(db.rpc("renew_action_outbox_lease", {
            "p_intent_id": intent_id, "p_workspace_id": workspace_id,
            "p_lease_token": claimed.get("lease_token"), "p_lease_seconds": 30,
        }))
        checks["stale_token_rejected"] = not bool(db.rpc("renew_action_outbox_lease", {
            "p_intent_id": intent_id, "p_workspace_id": workspace_id,
            "p_lease_token": "stale-" + uuid.uuid4().hex, "p_lease_seconds": 30,
        }))
        time.sleep(max(30, int(wait_seconds)))
        expired_claim = db.rpc("claim_action_outbox", {"p_limit": 1, "p_lease_seconds": 30, "p_worker_id": worker_b}) or []
        checks["expired_lease_reclaimable"] = bool(expired_claim) and expired_claim[0].get("lease_owner") == worker_b
        checks["no_provider_invoked"] = True
        ok = all(checks.values())
        return Report("passed" if ok else "failed", checks, intent_id)
    except Exception as exc:
        return Report("failed", checks, intent_id, str(exc))
    finally:
        db.delete_probe(intent_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="explicitly permit staging DB network calls")
    parser.add_argument("--wait-seconds", type=int, default=31)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps(asdict(Report("blocked", {}, error="pass --live to permit real staging network calls")), indent=2))
        return 2
    report = run_live(workspace_id=os.environ.get("KANYOZA_STAGING_WORKSPACE_ID", ""), wait_seconds=args.wait_seconds)
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
