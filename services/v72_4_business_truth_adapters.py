"""V72.4 — durable business-truth adapters for the Business Cockpit.

Read-only projections over authoritative V70/V71/V66 stores. Missing tables,
rows, or runtime surfaces remain explicit UNKNOWN/UNAVAILABLE; this module
never grants authority and never invokes execution/provider code.

V72.4.1 (2026-10-28) — WORKFORCE + DECISIONS FIXES:

  Bug 1 — Decisions query failed with "column decisions.updated_at does
  not exist". The `_section` helper defaults `order="updated_at"`, but the
  `decisions` table only has `created_at`. Passing `order="created_at"`
  explicitly fixes the query and lets the decisions section populate.

  Bug 2 — Workforce section reported count=0 despite 3 workers existing
  in agent_identities. The query targeted `agent_autonomous_runs` only,
  so it returned 0 when no runs had occurred. The section now queries
  BOTH `agent_identities` (the workforce itself) and `agent_autonomous_runs`
  (the workforce's execution history), returning workers and runs
  separately so the UI can render both.
"""
from __future__ import annotations

import hashlib, json
from datetime import datetime, timezone
from typing import Any, Mapping

from services.v72_2_command_center_read_model import SECTIONS, TruthValue


class BusinessTruthAdapters:
    VERSION = "v72.4.1"

    def __init__(self, workspace_id: str, db=None, runtime=None):
        self.workspace_id = str(workspace_id)
        self.db = db
        self.runtime = runtime

    @staticmethod
    def _fp(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _unknown(self, section: str, source: str, reason: str) -> TruthValue:
        return TruthValue(section, "UNKNOWN", "UNAVAILABLE", source, "unknown", "NONE",
                          ({"reason": reason},), {})

    def _query(self, table: str, *, limit: int = 25, order: str = "updated_at"):
        if self.db is None:
            raise RuntimeError("durable_store_unavailable")
        q = self.db.table(table).select("*").eq("workspace_id", self.workspace_id)
        try:
            q = q.order(order, desc=True)
        except Exception:
            pass
        return q.limit(limit).execute()

    def _rows(self, table: str, *, limit: int = 25, order: str = "updated_at") -> tuple[list[Mapping[str, Any]], str | None]:
        try:
            result = self._query(table, limit=limit, order=order)
            return list(getattr(result, "data", None) or []), None
        except Exception as exc:
            return [], str(exc)[:240]

    def _section(self, section: str, source: str, table: str, *, fields: Mapping[str, str], limit: int = 25,
                 order: str = "updated_at", summary: Mapping[str, Any] | None = None) -> TruthValue:
        rows, error = self._rows(table, limit=limit, order=order)
        if error:
            return self._unknown(section, source, f"{table}: {error}")
        compact = []
        for row in rows:
            item = {out: row.get(src) for out, src in fields.items() if src in row}
            compact.append(item)
        data = dict(summary or {})
        data["count"] = len(rows)
        data["items"] = compact[:10]
        evidence = tuple({"table": table, "rowFingerprint": self._fp(row)} for row in rows[:10])
        return TruthValue(section, "LIVE", "OBSERVED", source, self._now(), "HIGH", evidence, data)

    def _workforce_section(self) -> TruthValue:
        """Workforce section: workers exist independently of runs.

        The frontend's Workforce Operations page expects `workers` and
        `runs` separately. Querying only `agent_autonomous_runs` returns
        0 when no runs have occurred, even though workers exist.

        This queries both tables and returns the combined shape:
          - workers: agent_identities rows (with role, status, capabilities)
          - runs: agent_autonomous_runs rows
          - count: number of workers (not runs — the workforce exists)
          - activeRunCount: runs currently in-progress
        """
        workers, worker_error = self._rows(
            "agent_identities", limit=50, order="created_at"
        )
        runs, run_error = self._rows(
            "agent_autonomous_runs", limit=50, order="created_at"
        )

        errors = []
        if worker_error:
            errors.append(f"agent_identities: {worker_error}")
        if run_error:
            errors.append(f"agent_autonomous_runs: {run_error}")

        if errors:
            return self._unknown(
                "workforce", "governed_workforce", "; ".join(errors)
            )

        worker_items = [
            {
                "agentId": row.get("agent_id"),
                "name": row.get("name"),
                "role": (row.get("metadata") or {}).get("role"),
                "status": row.get("status"),
                "agentClass": row.get("agent_class"),
                "capabilities": row.get("declared_capabilities") or [],
            }
            for row in workers[:10]
        ]
        run_items = [
            {
                "runId": row.get("run_id"),
                "agentId": row.get("agent_id"),
                "workflowId": row.get("workflow_id"),
                "status": row.get("status"),
                "disposition": row.get("disposition"),
                "updatedAt": row.get("updated_at"),
            }
            for row in runs[:10]
        ]

        evidence = tuple(
            {"table": "agent_identities", "rowFingerprint": self._fp(row)}
            for row in workers[:10]
        ) + tuple(
            {"table": "agent_autonomous_runs", "rowFingerprint": self._fp(row)}
            for row in runs[:10]
        )

        return TruthValue(
            "workforce", "LIVE", "OBSERVED", "governed_workforce",
            self._now(), "HIGH", evidence,
            {
                "count": len(workers),
                "workerCount": len(workers),
                "activeRunCount": sum(
                    1 for r in runs if r.get("status") in ("running", "pending")
                ),
                "workers": worker_items,
                "runs": run_items,
                "items": worker_items,
            },
        )

    def build(self) -> dict[str, TruthValue]:
        out: dict[str, TruthValue] = {}
        # Runtime health is not a database fact; use the already-owned runtime status if available.
        if self.runtime is not None:
            try:
                status = self.runtime.status()
                out["businessHealth"] = TruthValue("businessHealth", "LIVE", "OBSERVED",
                    "production_runtime", self._now(), "HIGH",
                    ({"source": "production_runtime", "fingerprint": self._fp(status)},),
                    {"runtime": status})
            except Exception as exc:
                out["businessHealth"] = self._unknown("businessHealth", "production_runtime", str(exc)[:240])
        else:
            out["businessHealth"] = self._unknown("businessHealth", "production_runtime", "runtime_not_available")

        out["objectives"] = self._section("objectives", "business_operating_layer", "business_operating_objectives",
            fields={"objectiveId":"objective_id","objective":"objective","state":"state","envelopeId":"envelope_id"},
            order="created_at",
            summary={"activeCount": None})
        if out["objectives"].state == "LIVE":
            d = dict(out["objectives"].data); d["activeCount"] = sum(1 for x in d["items"] if x.get("state") == "active")
            out["objectives"] = TruthValue("objectives", "LIVE", "OBSERVED", "business_operating_layer", out["objectives"].freshness,
                out["objectives"].confidence, out["objectives"].evidence, d)

        out["opportunities"] = self._section("opportunities", "sales_pipeline", "sales_opportunity_operating_state",
            fields={"opportunityId":"opportunity_id","contact":"contact","amount":"amount","stage":"stage","probability":"probability","expectedCloseDate":"expected_close_date"},
            order="updated_at", summary={})
        out["risks"] = self._section("risks", "customer_and_internal_operations", "internal_business_work_state",
            fields={"workId":"work_id","title":"title","status":"status","priority":"priority","dueAt":"due_at","slaBreached":"sla_breached"},
            order="updated_at", summary={})
        # v72.4.1 FIX: decisions table has `created_at`, not `updated_at`.
        out["decisions"] = self._section("decisions", "governance", "decisions",
            fields={"decisionId":"decision_id","action":"action","status":"status","risk":"risk","createdAt":"created_at"},
            order="created_at",
            summary={})
        # v72.4.1 FIX: workforce must include agent_identities, not just runs.
        out["workforce"] = self._workforce_section()
        out["operations"] = self._section("operations", "business_operations", "internal_business_work_state",
            fields={"workId":"work_id","title":"title","status":"status","priority":"priority","ownerId":"owner_id","dueAt":"due_at"},
            order="updated_at", summary={})
        out["outcomes"] = self._section("outcomes", "outcome_measurement", "business_outcome_observations",
            fields={"observationId":"observation_id","executionId":"execution_id","outcomeState":"outcome_state","businessSuccess":"business_success","confidence":"business_success_confidence","observedAt":"observed_at"},
            order="observed_at", summary={})
        out["evidence"] = self._section("evidence", "outcome_and_execution_evidence", "business_outcome_attributions",
            fields={"attributionId":"attribution_id","executionId":"execution_id","status":"attribution_status","method":"attribution_method","businessSuccess":"business_success","confidence":"confidence","createdAt":"created_at"},
            order="created_at", summary={})
        out["governance"] = self._section("governance", "governance_and_certification", "business_decision_audit",
            fields={"decisionId":"decision_id","eventType":"event_type","risk":"risk","createdAt":"created_at"},
            order="created_at", summary={})
        return out


def build_business_truth(workspace_id: str, db=None, runtime=None) -> Mapping[str, TruthValue]:
    return BusinessTruthAdapters(workspace_id, db=db, runtime=runtime).build()
