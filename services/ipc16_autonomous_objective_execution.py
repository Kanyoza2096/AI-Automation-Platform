"""IPC-16 — governed autonomous objective-cycle execution.

Bridges IPC-15 replanning recommendations into the existing durable workforce
runtime. This module creates the *next durable run* only after a separate
objective-bound governance decision admits the cycle. It never executes a
provider and never grants authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from services.ipc15_objective_replanning import (
    ReplanDisposition,
    validate_replan_recommendation,
)

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ObjectiveCycleAdmission:
    cycle_id: str
    workspace_id: str
    objective_id: str
    source_replan_id: str
    run_id: str
    created: bool
    authority: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _fingerprint(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_governed_cycle_decision(*, decision: Mapping[str, Any], workspace_id: str, objective_id: str) -> None:
    if decision.get("authority") in (None, "none"):
        raise ValueError("governed cycle decision required")
    if decision.get("decision") not in {"allow", "approve"}:
        raise ValueError("governed cycle decision must allow or approve")
    if str(decision.get("workspace_id") or "") != str(workspace_id):
        raise ValueError("cycle decision workspace mismatch")
    if str(decision.get("objective_id") or "") != str(objective_id):
        raise ValueError("cycle decision objective mismatch")
    if not decision.get("decision_id"):
        raise ValueError("decision_id is required")


def admit_next_objective_cycle(
    *,
    recommendation: Mapping[str, Any],
    objective: Mapping[str, Any],
    governed_decision: Mapping[str, Any],
    source_run: Mapping[str, Any],
    workforce: Any,
    objective_context: Mapping[str, Any],
    db: Any = None,
) -> ObjectiveCycleAdmission:
    """Create the next durable workforce run after governed replan admission."""
    validate_replan_recommendation(recommendation)
    ws = str(objective.get("workspace_id") or "")
    oid = str(objective.get("objective_id") or "")
    if not ws or not oid:
        raise ValueError("objective workspace and identity are required")
    if recommendation.get("workspace_id") != ws or recommendation.get("objective_id") != oid:
        raise ValueError("recommendation workspace/objective binding mismatch")
    if not recommendation.get("next_cycle"):
        raise ValueError("replan does not request a next cycle")
    if recommendation.get("disposition") not in {
        ReplanDisposition.CONTINUE.value,
        ReplanDisposition.ADAPT.value,
        ReplanDisposition.RECOVER.value,
    }:
        raise ValueError("replan disposition cannot create an autonomous cycle")
    _validate_governed_cycle_decision(decision=governed_decision, workspace_id=ws, objective_id=oid)
    if str(source_run.get("workspace_id") or "") != ws:
        raise ValueError("source run workspace mismatch")
    if str(source_run.get("objective_id") or "") != oid:
        raise ValueError("source run objective mismatch")

    from services.business_objective_context import validate_objective_context
    validate_objective_context(objective_context, workspace_id=ws)
    if str(objective_context.get("objective_id")) != oid:
        raise ValueError("objective context mismatch")

    cycle_seed = {
        "workspace_id": ws,
        "objective_id": oid,
        "replan_id": recommendation["replan_id"],
        "decision_id": governed_decision["decision_id"],
        "source_run_id": source_run.get("run_id"),
    }
    cycle_id = "cycle_" + _fingerprint(cycle_seed)[:24]
    run_id = "run_" + _fingerprint({**cycle_seed, "cycle_id": cycle_id})[:24]
    event = dict(source_run.get("event") or {})
    event.update({
        "objective_id": oid,
        "_kdo_objective_context": dict(objective_context),
        "_kdo_replan": {
            "replan_id": recommendation["replan_id"],
            "fingerprint": recommendation["fingerprint"],
            "disposition": recommendation["disposition"],
            "source_verification_id": recommendation["source_verification_id"],
            "cycle_id": cycle_id,
            "governed_decision_id": governed_decision["decision_id"],
            "authority": "none",
        },
    })
    run = workforce.create(
        run_id=run_id,
        agent_id=str(source_run["agent_id"]),
        plan_id=str(source_run["plan_id"]),
        objective=str(source_run["objective"]),
        workflow_id=str(source_run["workflow_id"]),
        trigger=f"objective_replan:{recommendation['replan_id']}",
        event=event,
        budget_id=source_run.get("budget_id"),
        coordination_id=source_run.get("coordination_id"),
    )
    # Creation is durable intent only. Existing V71.9 admission must still
    # evaluate plan/budget/workflow governance before the run becomes READY.
    admission = ObjectiveCycleAdmission(cycle_id, ws, oid, str(recommendation["replan_id"]), run.run_id, True)
    if db is not None:
        row = {
            "cycle_id": admission.cycle_id,
            "workspace_id": ws,
            "objective_id": oid,
            "source_replan_id": admission.source_replan_id,
            "run_id": admission.run_id,
            "governed_decision_id": str(governed_decision["decision_id"]),
            "status": "created",
            "cycle_fingerprint": _fingerprint(cycle_seed),
        }
        try:
            result = db.table("objective_execution_cycles").insert(row).select("*").execute()
            if not getattr(result, "data", None):
                raise RuntimeError("objective cycle persistence failed")
        except Exception as exc:
            # A uniqueness conflict is safe only when the existing row matches
            # this deterministic cycle identity; otherwise fail closed.
            try:
                existing = (db.table("objective_execution_cycles").select("*")
                            .eq("workspace_id", ws).eq("source_replan_id", admission.source_replan_id)
                            .limit(1).execute())
                rows = list(getattr(existing, "data", None) or [])
                if not rows or str(rows[0].get("cycle_id")) != admission.cycle_id:
                    raise RuntimeError("objective cycle persistence conflict") from exc
            except RuntimeError:
                raise
            except Exception as read_exc:
                raise RuntimeError("objective cycle persistence unavailable") from read_exc
    return admission


def validate_cycle_lineage(*, run: Mapping[str, Any], recommendation: Mapping[str, Any], workspace_id: str, objective_id: str) -> None:
    validate_replan_recommendation(recommendation)
    if str(run.get("workspace_id") or "") != str(workspace_id):
        raise ValueError("cycle run workspace mismatch")
    if str(run.get("objective_id") or "") != str(objective_id):
        raise ValueError("cycle run objective mismatch")
    marker = dict(run.get("event") or {}).get("_kdo_replan")
    if not isinstance(marker, Mapping) or marker.get("replan_id") != recommendation.get("replan_id"):
        raise ValueError("cycle replan lineage mismatch")
    if marker.get("fingerprint") != recommendation.get("fingerprint"):
        raise ValueError("cycle replan fingerprint mismatch")
    if marker.get("authority") != "none":
        raise ValueError("cycle lineage cannot grant authority")
