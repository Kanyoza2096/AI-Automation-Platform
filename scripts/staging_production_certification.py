#!/usr/bin/env python3
"""Live staging certification for KDO durable execution primitives.

This runner exercises the *real* staging ActionOutbox RPCs through Supabase's
PostgREST endpoint. It deliberately performs no business-provider mutation.

Required with --live:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  KANYOZA_STAGING_WORKSPACE_ID
  KANYOZA_STAGING_SECOND_WORKSPACE_ID (recommended for tenant-fence proof)

The runner is fail-closed. Without --live it never performs network I/O.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any
from urllib import error, parse, request


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class Report:
    status: str
    checks: list[dict[str, Any]]
    intent_id: str = ""
    error: str = ""


class SupabaseREST:
    def __init__(self, base_url: str, service_key: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any | None = None, *, prefer: str | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        req = request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail[:1200]}") from exc

    def rpc(self, name: str, args: dict[str, Any]) -> Any:
        return self._request("POST", f"/rest/v1/rpc/{name}", args)

    def insert(self, row: dict[str, Any]) -> None:
        self._request("POST", "/rest/v1/action_outbox", row, prefer="return=minimal")

    def select(self, intent_id: str) -> list[dict[str, Any]]:
        encoded = parse.quote(intent_id, safe="")
        return self._request("GET", f"/rest/v1/action_outbox?intent_id=eq.{encoded}&select=*") or []

    def delete(self, intent_id: str) -> None:
        encoded = parse.quote(intent_id, safe="")
        try:
            self._request("DELETE", f"/rest/v1/action_outbox?intent_id=eq.{encoded}")
        except RuntimeError:
            pass


def _claim(db: SupabaseREST, worker_id: str) -> list[dict[str, Any]]:
    return db.rpc(
        "claim_action_outbox",
        {"p_limit": 1, "p_lease_seconds": 30, "p_worker_id": worker_id},
    ) or []


def run_live(workspace_id: str, second_workspace_id: str | None, wait_seconds: int) -> Report:
    base = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key or not workspace_id:
        return Report("blocked", [], error="SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and KANYOZA_STAGING_WORKSPACE_ID are required")

    db = SupabaseREST(base, key)
    intent_id = "kdo-prod-cert-" + uuid.uuid4().hex
    idem = "kdo-prod-cert-" + uuid.uuid4().hex
    worker_a = "cert-a-" + uuid.uuid4().hex[:12]
    worker_b = "cert-b-" + uuid.uuid4().hex[:12]
    checks: list[Check] = []
    blocked_reasons: list[str] = []

    row = {
        "intent_id": intent_id,
        "workspace_id": workspace_id,
        "action_type": "__kdo_production_certification_probe__",
        "parameters": {"external_side_effect": False, "purpose": "production-concurrency-certification"},
        "risk_level": "low",
        "idempotency_key": idem,
        "source": "production-certification-runner",
        "status": "pending",
    }

    try:
        db.insert(row)

        # Real concurrent race: both workers hit the same PostgreSQL RPC at once.
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(_claim, db, worker_a)
            future_b = pool.submit(_claim, db, worker_b)
            claimed_a = future_a.result()
            claimed_b = future_b.result()

        owners = [r.get("lease_owner") for r in (claimed_a + claimed_b)]
        checks.append(Check(
            "concurrent_claim_single_owner",
            len(claimed_a) + len(claimed_b) == 1 and owners[0] in {worker_a, worker_b},
            {"worker_a_claims": len(claimed_a), "worker_b_claims": len(claimed_b), "owners": owners},
        ))

        claimed = (claimed_a or claimed_b)[0] if (claimed_a or claimed_b) else None
        if not claimed:
            return Report("failed", [asdict(c) for c in checks], intent_id, "neither concurrent worker claimed the probe")

        token = str(claimed.get("lease_token") or "")
        owner = str(claimed.get("lease_owner") or "")
        checks.append(Check(
            "lease_owner_and_token_issued",
            owner in {worker_a, worker_b} and bool(token) and claimed.get("status") == "processing",
            {"owner": owner, "has_token": bool(token), "status": claimed.get("status")},
        ))

        competitor = worker_b if owner == worker_a else worker_a
        competitor_claim = _claim(db, competitor)
        checks.append(Check(
            "live_lease_blocks_competitor",
            not competitor_claim,
            {"competitor_claim_count": len(competitor_claim)},
        ))

        renewed = db.rpc("renew_action_outbox_lease", {
            "p_intent_id": intent_id,
            "p_workspace_id": workspace_id,
            "p_lease_token": token,
            "p_lease_seconds": 30,
        })
        checks.append(Check("current_token_renews", bool(renewed), {"renewed": bool(renewed)}))

        stale = db.rpc("renew_action_outbox_lease", {
            "p_intent_id": intent_id,
            "p_workspace_id": workspace_id,
            "p_lease_token": "stale-" + uuid.uuid4().hex,
            "p_lease_seconds": 30,
        })
        checks.append(Check("stale_token_rejected", not bool(stale), {"stale_renewed": bool(stale)}))

        if second_workspace_id:
            cross = db.rpc("finalize_action_outbox", {
                "p_intent_id": intent_id,
                "p_workspace_id": second_workspace_id,
                "p_lease_token": token,
                "p_status": "failed",
                "p_error": "cross-workspace certification probe",
            })
            checks.append(Check("cross_workspace_finalize_rejected", not bool(cross), {"accepted": bool(cross)}))
        else:
            checks.append(Check("cross_workspace_finalize_rejected", False, {"skipped": "second workspace not configured"}))
            blocked_reasons.append("KANYOZA_STAGING_SECOND_WORKSPACE_ID is required for tenant-fence proof")

        sleep_for = max(30, wait_seconds)
        time.sleep(sleep_for)
        expired = _claim(db, competitor)
        expired_owner = expired[0].get("lease_owner") if expired else None
        checks.append(Check(
            "expired_lease_reclaimable",
            bool(expired) and expired_owner == competitor,
            {"reclaimed": bool(expired), "owner": expired_owner, "wait_seconds": sleep_for},
        ))

        # We intentionally never call a provider. This is a control-plane proof.
        checks.append(Check("no_provider_invoked", True, {"provider_calls": 0, "action_type": row["action_type"]}))

        status = "passed" if all(c.passed for c in checks) else ("not_proven" if blocked_reasons else "failed")
        return Report(status, [asdict(c) for c in checks], intent_id, "; ".join(blocked_reasons))
    except Exception as exc:
        return Report("failed", [asdict(c) for c in checks], intent_id, str(exc))
    finally:
        db.delete(intent_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly permit staging network calls")
    parser.add_argument("--wait-seconds", type=int, default=31)
    parser.add_argument("--workspace-id", default=os.environ.get("KANYOZA_STAGING_WORKSPACE_ID", ""))
    parser.add_argument("--second-workspace-id", default=os.environ.get("KANYOZA_STAGING_SECOND_WORKSPACE_ID", ""))
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps(asdict(Report("blocked", [], error="pass --live to permit real staging network calls")), indent=2))
        return 2
    report = run_live(args.workspace_id, args.second_workspace_id or None, args.wait_seconds)
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.status == "passed" else (2 if report.status == "not_proven" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
