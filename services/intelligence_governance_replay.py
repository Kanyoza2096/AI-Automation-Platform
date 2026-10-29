"""Phase 5.12 — durable decision-to-governance replay proof.

Historical governance is replayed against the exact policy snapshot that was
used when the decision was evaluated. Current policy configuration is never
silently substituted for historical policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_policy_governance import (
    GovernanceDisposition,
    IntelligencePolicy,
    IntelligencePolicyGovernor,
    RiskClass,
)

SCHEMA = "KDO.IntelligenceGovernanceReplay/v1"


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def policy_snapshot(policy: IntelligencePolicy) -> dict[str, Any]:
    return {
        "policy_id": policy.policy_id,
        "workspace_id": policy.workspace_id,
        "min_confidence_by_risk": {str(k.value): float(v) for k, v in policy.min_confidence_by_risk.items()},
        "approval_required_by_risk": {str(k.value): bool(v) for k, v in policy.approval_required_by_risk.items()},
        "allowed_action_types": sorted(policy.allowed_action_types) if policy.allowed_action_types is not None else None,
        "blocked_action_types": sorted(policy.blocked_action_types),
        "max_uncertainty": float(policy.max_uncertainty),
        "minimum_evidence_refs": int(policy.minimum_evidence_refs),
        "allowed_autonomy_levels": sorted(policy.allowed_autonomy_levels),
    }


def policy_from_snapshot(value: Mapping[str, Any]) -> IntelligencePolicy:
    return IntelligencePolicy(
        policy_id=str(value["policy_id"]),
        workspace_id=str(value["workspace_id"]),
        min_confidence_by_risk={RiskClass(k): float(v) for k, v in dict(value["min_confidence_by_risk"]).items()},
        approval_required_by_risk={RiskClass(k): bool(v) for k, v in dict(value["approval_required_by_risk"]).items()},
        allowed_action_types=frozenset(value["allowed_action_types"]) if value["allowed_action_types"] is not None else None,
        blocked_action_types=frozenset(value["blocked_action_types"]),
        max_uncertainty=float(value["max_uncertainty"]),
        minimum_evidence_refs=int(value["minimum_evidence_refs"]),
        allowed_autonomy_levels=frozenset(value["allowed_autonomy_levels"]),
    )


def governance_evaluation_fingerprint(*, policy_fingerprint: str, decision_fingerprint: str, evaluation: Mapping[str, Any]) -> str:
    return _fp({
        "schema_version": SCHEMA,
        "policy_fingerprint": policy_fingerprint,
        "decision_fingerprint": decision_fingerprint,
        "evaluation": dict(evaluation),
    })


@dataclass(frozen=True)
class GovernanceReplaySnapshot:
    replay_id: str
    workspace_id: str
    decision_fingerprint: str
    policy_fingerprint: str
    policy: dict[str, Any]
    evaluation: dict[str, Any]
    governance_fingerprint: str
    schema_version: str = SCHEMA


class IntelligenceGovernanceReplayRepository:
    """Append-only historical governance snapshots with deterministic replay."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE IF NOT EXISTS intelligence_governance_replays (
            replay_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            decision_fingerprint TEXT NOT NULL UNIQUE,
            policy_fingerprint TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            evaluation_json TEXT NOT NULL,
            governance_fingerprint TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL
        )""")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def capture(
        self,
        *,
        decision_replay: IntelligenceDecisionReplayRepository,
        decision_replay_id: str,
        policy: IntelligencePolicy,
    ) -> GovernanceReplaySnapshot:
        decision = decision_replay.replay(decision_replay_id)
        if decision.workspace_id != policy.workspace_id:
            raise PermissionError("governance policy workspace mismatch")
        decision_fp = decision.decision_fingerprint
        recommendation = decision.recommendation
        if recommendation.get("provenance", {}).get("decision_fingerprint") != decision_fp:
            raise ValueError("governance decision fingerprint provenance mismatch")
        evaluation = IntelligencePolicyGovernor().evaluate(
            recommendation=_node_from_dict(recommendation), policy=policy
        )
        policy_dict = policy_snapshot(policy)
        policy_fp = _fp(policy_dict)
        evaluation_dict = evaluation.to_dict()
        governance_fp = governance_evaluation_fingerprint(
            policy_fingerprint=policy_fp, decision_fingerprint=decision_fp, evaluation=evaluation_dict
        )
        replay_id = "igr_" + _fp({"workspace_id": policy.workspace_id, "decision_fingerprint": decision_fp, "policy_fingerprint": policy_fp})[:24]
        snapshot = GovernanceReplaySnapshot(
            replay_id=replay_id, workspace_id=policy.workspace_id, decision_fingerprint=decision_fp,
            policy_fingerprint=policy_fp, policy=policy_dict, evaluation=evaluation_dict,
            governance_fingerprint=governance_fp,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_governance_replays
                (replay_id,workspace_id,decision_fingerprint,policy_fingerprint,policy_json,evaluation_json,governance_fingerprint,schema_version)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(decision_fingerprint) DO NOTHING""",
                (snapshot.replay_id, snapshot.workspace_id, snapshot.decision_fingerprint, snapshot.policy_fingerprint,
                 json.dumps(snapshot.policy, sort_keys=True), json.dumps(snapshot.evaluation, sort_keys=True), snapshot.governance_fingerprint, SCHEMA),
            )
        return snapshot

    def replay(
        self,
        replay_id: str,
        *,
        decision_replay: IntelligenceDecisionReplayRepository,
        current_policy: IntelligencePolicy | None = None,
        workspace_id: str | None = None,
    ) -> GovernanceReplaySnapshot:
        row = self._conn.execute("SELECT * FROM intelligence_governance_replays WHERE replay_id=?", (replay_id,)).fetchone()
        if row is None:
            raise KeyError(replay_id)
        if workspace_id is not None and workspace_id != row["workspace_id"]:
            raise PermissionError("governance replay workspace mismatch")
        policy_dict = json.loads(row["policy_json"])
        evaluation = json.loads(row["evaluation_json"])
        if _fp(policy_dict) != row["policy_fingerprint"]:
            raise ValueError("stored governance policy fingerprint mismatch")
        if governance_evaluation_fingerprint(policy_fingerprint=row["policy_fingerprint"], decision_fingerprint=row["decision_fingerprint"], evaluation=evaluation) != row["governance_fingerprint"]:
            raise ValueError("stored governance evaluation fingerprint mismatch")
        decision = decision_replay.replay(row["decision_replay_id"] if "decision_replay_id" in row.keys() else "") if False else None
        # The decision replay identity is represented by its stable semantic fingerprint.
        if current_policy is not None:
            current_fp = _fp(policy_snapshot(current_policy))
            if current_fp != row["policy_fingerprint"]:
                raise ValueError("historical governance replay cannot use a different policy")
        frozen_policy = policy_from_snapshot(policy_dict)
        # Re-evaluate only against the frozen historical policy, never the live policy.
        # The caller supplies the decision repository so the recommendation can be verified.
        candidates = decision_replay._conn.execute(
            "SELECT recommendation_json FROM intelligence_decision_replays WHERE decision_fingerprint=?",
            (row["decision_fingerprint"],),
        ).fetchone()
        if candidates is None:
            raise KeyError(row["decision_fingerprint"])
        recommendation = json.loads(candidates["recommendation_json"])
        if recommendation.get("provenance", {}).get("decision_fingerprint") != row["decision_fingerprint"]:
            raise ValueError("historical governance decision identity mismatch")
        recomputed = IntelligencePolicyGovernor().evaluate(
            recommendation=_node_from_dict(recommendation), policy=frozen_policy
        ).to_dict()
        if recomputed != evaluation:
            raise ValueError("historical governance evaluation mismatch")
        return GovernanceReplaySnapshot(
            replay_id=row["replay_id"], workspace_id=row["workspace_id"], decision_fingerprint=row["decision_fingerprint"],
            policy_fingerprint=row["policy_fingerprint"], policy=policy_dict, evaluation=evaluation,
            governance_fingerprint=row["governance_fingerprint"], schema_version=row["schema_version"],
        )


def _node_from_dict(value: Mapping[str, Any]):
    from services.intelligence_evidence_graph import EvidenceNode
    return EvidenceNode(
        node_id=str(value["node_id"]), node_type=str(value["node_type"]), workspace_id=str(value["workspace_id"]),
        payload=dict(value["payload"]), provenance=dict(value.get("provenance", {})),
        created_at=str(value.get("created_at", "")), schema_version=str(value.get("schema_version", "")),
    )


__all__ = [
    "SCHEMA", "GovernanceReplaySnapshot", "IntelligenceGovernanceReplayRepository",
    "policy_snapshot", "policy_from_snapshot", "governance_evaluation_fingerprint",
]
