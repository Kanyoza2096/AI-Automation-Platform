"""Phase 5.13 — governed ActionIntent replay proof.

Binds an intelligence ActionIntent to the exact historical governance proof
that admitted it. Replay is read-only and never authorizes or executes work.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
from typing import Any

from services.intelligence_action_intent_admission import IntelligenceActionIntentAdmissionGate
from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph
from services.intelligence_governance_replay import IntelligenceGovernanceReplayRepository, GovernanceReplaySnapshot, policy_from_snapshot

SCHEMA = "KDO.IntelligenceActionIntentReplay/v1"


@dataclass(frozen=True)
class ActionIntentReplaySnapshot:
    replay_id: str
    workspace_id: str
    action_intent_node_id: str
    action_intent_fingerprint: str
    governance_replay_id: str
    governance_fingerprint: str
    policy_fingerprint: str
    decision_fingerprint: str
    recommendation_node_id: str
    admission_fingerprint: str
    action_intent: dict[str, Any]
    schema_version: str = SCHEMA


class IntelligenceActionIntentReplayRepository:
    """Append-only proof that an ActionIntent came from historical governance."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS intelligence_action_intent_replays (
                replay_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                action_intent_node_id TEXT NOT NULL,
                action_intent_fingerprint TEXT NOT NULL UNIQUE,
                governance_replay_id TEXT NOT NULL,
                governance_fingerprint TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                decision_fingerprint TEXT NOT NULL,
                recommendation_node_id TEXT NOT NULL,
                admission_fingerprint TEXT NOT NULL,
                action_intent_json TEXT NOT NULL,
                schema_version TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def capture(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        action_intent_node_id: str,
        governance_replay: IntelligenceGovernanceReplayRepository,
        governance_replay_id: str,
        decision_replay: Any,
    ) -> ActionIntentReplaySnapshot:
        governance = governance_replay.replay(governance_replay_id, decision_replay=decision_replay)
        action = graph.get_node(action_intent_node_id)
        self._validate_binding(action, governance)

        # Re-run the existing admission gate against the frozen historical policy.
        policy = policy_from_snapshot(governance.policy)
        from services.intelligence_policy_governance import GovernanceEvaluation, GovernanceDisposition, RiskClass
        evaluation = GovernanceEvaluation(
            policy_id=governance.evaluation["policy_id"],
            workspace_id=governance.evaluation["workspace_id"],
            recommendation_node_id=governance.evaluation["recommendation_node_id"],
            action_type=governance.evaluation["action_type"],
            risk_class=RiskClass(governance.evaluation["risk_class"]),
            confidence=float(governance.evaluation["confidence"]),
            uncertainty=float(governance.evaluation["uncertainty"]),
            evidence_ref_count=int(governance.evaluation["evidence_ref_count"]),
            autonomy_level=governance.evaluation["autonomy_level"],
            disposition=GovernanceDisposition(governance.evaluation["disposition"]),
            reasons=tuple(governance.evaluation["reasons"]),
            requires_human_approval=bool(governance.evaluation["requires_human_approval"]),
            governed=bool(governance.evaluation["governed"]),
        )
        admission = IntelligenceActionIntentAdmissionGate().admit(
            graph=graph,
            action_intent_node_id=action_intent_node_id,
            policy=policy,
            evaluation=evaluation,
        )
        if admission.disposition.value != "APPROVED":
            raise ValueError("historical ActionIntent is not approved")

        action_dict = action.to_dict()
        action_fp = action_dict["fingerprint"]
        replay_id = "aiir_" + action_fp[:24]
        snapshot = ActionIntentReplaySnapshot(
            replay_id=replay_id,
            workspace_id=action.workspace_id,
            action_intent_node_id=action.node_id,
            action_intent_fingerprint=action_fp,
            governance_replay_id=governance.replay_id,
            governance_fingerprint=governance.governance_fingerprint,
            policy_fingerprint=governance.policy_fingerprint,
            decision_fingerprint=governance.decision_fingerprint,
            recommendation_node_id=governance.evaluation["recommendation_node_id"],
            admission_fingerprint=admission.admission_fingerprint,
            action_intent=action_dict,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_action_intent_replays
                (replay_id,workspace_id,action_intent_node_id,action_intent_fingerprint,
                 governance_replay_id,governance_fingerprint,policy_fingerprint,decision_fingerprint,
                 recommendation_node_id,admission_fingerprint,action_intent_json,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(action_intent_fingerprint) DO NOTHING""",
                (
                    snapshot.replay_id, snapshot.workspace_id, snapshot.action_intent_node_id,
                    snapshot.action_intent_fingerprint, snapshot.governance_replay_id,
                    snapshot.governance_fingerprint, snapshot.policy_fingerprint,
                    snapshot.decision_fingerprint, snapshot.recommendation_node_id,
                    snapshot.admission_fingerprint, json.dumps(snapshot.action_intent, sort_keys=True), SCHEMA,
                ),
            )
        return snapshot

    def replay(
        self,
        replay_id: str,
        *,
        governance_replay: IntelligenceGovernanceReplayRepository,
        decision_replay: Any,
        graph: IntelligenceEvidenceGraph | None = None,
        workspace_id: str | None = None,
    ) -> ActionIntentReplaySnapshot:
        row = self._conn.execute(
            "SELECT * FROM intelligence_action_intent_replays WHERE replay_id=?", (replay_id,)
        ).fetchone()
        if row is None:
            raise KeyError(replay_id)
        if workspace_id is not None and workspace_id != row["workspace_id"]:
            raise PermissionError("ActionIntent replay workspace mismatch")

        action_dict = json.loads(row["action_intent_json"])
        stored_fp = row["action_intent_fingerprint"]
        if _action_fingerprint(action_dict) != stored_fp:
            raise ValueError("stored ActionIntent fingerprint mismatch")
        if action_dict.get("workspace_id") != row["workspace_id"]:
            raise PermissionError("stored ActionIntent workspace mismatch")
        if action_dict.get("node_type") != "action_intent":
            raise ValueError("historical replay target is not an ActionIntent")

        governance = governance_replay.replay(
            row["governance_replay_id"], decision_replay=decision_replay, workspace_id=row["workspace_id"]
        )
        if governance.governance_fingerprint != row["governance_fingerprint"]:
            raise ValueError("ActionIntent governance fingerprint mismatch")
        if governance.policy_fingerprint != row["policy_fingerprint"]:
            raise ValueError("ActionIntent policy fingerprint mismatch")
        if governance.decision_fingerprint != row["decision_fingerprint"]:
            raise ValueError("ActionIntent decision fingerprint mismatch")
        if governance.evaluation["recommendation_node_id"] != row["recommendation_node_id"]:
            raise ValueError("ActionIntent recommendation lineage mismatch")

        action = _node_from_dict(action_dict)
        self._validate_binding(action, governance)
        expected_admission = action.to_dict()["fingerprint"]
        if expected_admission != row["admission_fingerprint"]:
            raise ValueError("historical ActionIntent admission fingerprint mismatch")

        if graph is not None:
            current = graph.get_node(row["action_intent_node_id"])
            if current.workspace_id != row["workspace_id"]:
                raise PermissionError("current ActionIntent workspace mismatch")
            if current.to_dict()["fingerprint"] != stored_fp:
                raise ValueError("current ActionIntent differs from historical admitted intent")

        return ActionIntentReplaySnapshot(
            replay_id=row["replay_id"], workspace_id=row["workspace_id"],
            action_intent_node_id=row["action_intent_node_id"], action_intent_fingerprint=stored_fp,
            governance_replay_id=row["governance_replay_id"], governance_fingerprint=row["governance_fingerprint"],
            policy_fingerprint=row["policy_fingerprint"], decision_fingerprint=row["decision_fingerprint"],
            recommendation_node_id=row["recommendation_node_id"], admission_fingerprint=row["admission_fingerprint"],
            action_intent=action_dict, schema_version=row["schema_version"],
        )

    @staticmethod
    def _validate_binding(action: EvidenceNode, governance: GovernanceReplaySnapshot) -> None:
        if action.node_type != "action_intent":
            raise ValueError("replay target must be an action_intent")
        if action.workspace_id != governance.workspace_id:
            raise PermissionError("ActionIntent/governance workspace mismatch")
        evaluation = governance.evaluation
        if action.provenance.get("recommendation_node_id") != evaluation["recommendation_node_id"]:
            raise ValueError("ActionIntent recommendation lineage mismatch")
        if action.payload.get("action_type") != evaluation["action_type"]:
            raise ValueError("ActionIntent action_type does not match historical governance")
        if float(action.payload.get("confidence", -1.0)) != float(evaluation["confidence"]):
            raise ValueError("ActionIntent confidence does not match historical governance")
        if float(action.payload.get("uncertainty", -1.0)) != float(evaluation["uncertainty"]):
            raise ValueError("ActionIntent uncertainty does not match historical governance")
        governance_payload = action.payload.get("governance")
        if not isinstance(governance_payload, dict):
            raise ValueError("ActionIntent governance evidence is missing")
        if governance_payload.get("policy_id") != governance.policy["policy_id"]:
            raise ValueError("ActionIntent governance policy substitution detected")
        if governance_payload.get("disposition") != evaluation["disposition"]:
            raise ValueError("ActionIntent governance disposition mismatch")
        if action.provenance.get("governance_policy_id") != governance.policy["policy_id"]:
            raise ValueError("ActionIntent governance provenance policy mismatch")
        if action.provenance.get("governance_disposition") != evaluation["disposition"]:
            raise ValueError("ActionIntent governance provenance disposition mismatch")


def _action_fingerprint(value: dict[str, Any]) -> str:
    import hashlib
    canonical = dict(value)
    canonical.pop("fingerprint", None)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _node_from_dict(value: dict[str, Any]) -> EvidenceNode:
    return EvidenceNode(
        node_id=str(value["node_id"]), node_type=str(value["node_type"]),
        workspace_id=str(value["workspace_id"]), payload=dict(value["payload"]),
        provenance=dict(value.get("provenance", {})), created_at=str(value.get("created_at", "")),
        schema_version=str(value.get("schema_version", "")), fingerprint=str(value.get("fingerprint", "")),
    )


__all__ = ["SCHEMA", "ActionIntentReplaySnapshot", "IntelligenceActionIntentReplayRepository"]
