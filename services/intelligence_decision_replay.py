"""Phase 5.6 — durable decision snapshots and deterministic replay.

Historical decisions replay from their captured semantic snapshot and learning
context identity. Replay never re-reads newer learning feedback and never
re-enters governance or execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from services.intelligence_decision_engine import IntelligenceDecisionResult, decision_semantic_fingerprint
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph, SCHEMA_VERSION
from services.intelligence_ai_fabric import interpretation_semantic_fingerprint

SCHEMA = "KDO.IntelligenceDecisionReplay/v2"


def _fp(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class DecisionReplaySnapshot:
    replay_id: str
    workspace_id: str
    decision_fingerprint: str
    learning_context_id: str | None
    learning_source_fingerprints: tuple[str, ...]
    ai_receipt_fingerprint: str | None
    ai_provider: str | None
    ai_model: str | None
    ai_correlation_id: str | None
    ai_request_fingerprint: str | None
    ai_status: str | None
    ai_interpretation_semantic_fingerprint: str | None
    decision: dict[str, Any]
    recommendation: dict[str, Any]
    interpretation: dict[str, Any]


class IntelligenceDecisionReplayRepository:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE IF NOT EXISTS intelligence_decision_replays (
            replay_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            decision_fingerprint TEXT NOT NULL UNIQUE,
            learning_context_id TEXT,
            learning_source_fingerprints_json TEXT NOT NULL,
            ai_receipt_fingerprint TEXT,
            ai_provider TEXT,
            ai_model TEXT,
            ai_correlation_id TEXT,
            ai_request_fingerprint TEXT,
            ai_status TEXT,
            ai_interpretation_semantic_fingerprint TEXT,
            decision_json TEXT NOT NULL,
            recommendation_json TEXT NOT NULL,
            interpretation_json TEXT NOT NULL,
            schema_version TEXT NOT NULL
        )""")
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(intelligence_decision_replays)").fetchall()}
        for column in ("ai_receipt_fingerprint", "ai_provider", "ai_model", "ai_correlation_id", "ai_request_fingerprint", "ai_status", "ai_interpretation_semantic_fingerprint", "interpretation_json"):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE intelligence_decision_replays ADD COLUMN {column} TEXT")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def capture(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        result: IntelligenceDecisionResult,
    ) -> DecisionReplaySnapshot:
        decision = result.decision.to_dict()
        recommendation = result.recommendation.to_dict()
        interpretation_id = str(decision["provenance"].get("interpretation_node_id") or "")
        if not interpretation_id:
            raise ValueError("decision interpretation identity is required")
        interpretation = graph.get_node(interpretation_id).to_dict()
        if decision["workspace_id"] != recommendation["workspace_id"]:
            raise ValueError("decision/recommendation workspace mismatch")
        provenance = recommendation["provenance"]
        ai_keys = ("ai_receipt_fingerprint", "ai_interpretation_semantic_fingerprint", "ai_provider", "ai_model", "ai_correlation_id", "ai_request_fingerprint", "ai_status")
        ai_values = {key: provenance.get(key) for key in ai_keys}
        decision_fingerprint = str(provenance.get("decision_fingerprint") or "")
        if not decision_fingerprint:
            raise ValueError("decision fingerprint is required")
        if decision["provenance"].get("decision_fingerprint") != decision_fingerprint:
            raise ValueError("decision fingerprint provenance mismatch")
        # Ensure the captured decision is still part of a valid evidence graph.
        snapshot = graph.snapshot()
        ids = {n["node_id"] for n in snapshot["nodes"]}
        if decision["node_id"] not in ids or recommendation["node_id"] not in ids:
            raise ValueError("decision is not present in supplied evidence graph")
        reconstructed = decision_semantic_fingerprint(
            policy_id=str(provenance.get("learning_use_policy_id") or ""),
            workspace_id=decision["workspace_id"],
            learning_use_mode=str(provenance.get("learning_use_mode") or ""),
            learning_context_id=provenance.get("learning_context_id"),
            learning_source_fingerprints=tuple(provenance.get("learning_source_fingerprints", [])),
            ai_provenance={key: ai_values[key] for key in ai_values},
            interpretation=interpretation,
            decision=decision["payload"],
            recommendation=recommendation["payload"],
        )
        if reconstructed != decision_fingerprint:
            raise ValueError("decision semantic fingerprint mismatch")
        replay_id = "idr_" + _fp({"workspace_id": decision["workspace_id"], "decision_fingerprint": decision_fingerprint})[:24]
        row = DecisionReplaySnapshot(
            replay_id=replay_id,
            workspace_id=decision["workspace_id"],
            decision_fingerprint=decision_fingerprint,
            learning_context_id=provenance.get("learning_context_id"),
            learning_source_fingerprints=tuple(provenance.get("learning_source_fingerprints", [])),
            ai_receipt_fingerprint=ai_values["ai_receipt_fingerprint"],
            ai_provider=ai_values["ai_provider"],
            ai_model=ai_values["ai_model"],
            ai_correlation_id=ai_values["ai_correlation_id"],
            ai_request_fingerprint=ai_values["ai_request_fingerprint"],
            ai_status=ai_values["ai_status"],
            ai_interpretation_semantic_fingerprint=ai_values["ai_interpretation_semantic_fingerprint"],
            decision=decision,
            recommendation=recommendation,
            interpretation=interpretation,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_decision_replays
                (replay_id,workspace_id,decision_fingerprint,learning_context_id,learning_source_fingerprints_json,ai_receipt_fingerprint,ai_provider,ai_model,ai_correlation_id,ai_request_fingerprint,ai_status,ai_interpretation_semantic_fingerprint,decision_json,recommendation_json,interpretation_json,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(decision_fingerprint) DO NOTHING""",
                (row.replay_id, row.workspace_id, row.decision_fingerprint, row.learning_context_id,
                 json.dumps(list(row.learning_source_fingerprints), sort_keys=True), row.ai_receipt_fingerprint, row.ai_provider, row.ai_model, row.ai_correlation_id, row.ai_request_fingerprint, row.ai_status, row.ai_interpretation_semantic_fingerprint,
                 json.dumps(row.decision, sort_keys=True),
                 json.dumps(row.recommendation, sort_keys=True), json.dumps(row.interpretation, sort_keys=True), SCHEMA),
            )
        return row

    def replay(self, replay_id: str, *, current_learning_context: Mapping[str, Any] | None = None, ai_receipt_repository: Any | None = None, workspace_id: str | None = None) -> DecisionReplaySnapshot:
        row = self._conn.execute("SELECT * FROM intelligence_decision_replays WHERE replay_id=?", (replay_id,)).fetchone()
        if row is None:
            raise KeyError(replay_id)
        source_fps = tuple(json.loads(row["learning_source_fingerprints_json"]))
        interpretation = json.loads(row["interpretation_json"]) if row["interpretation_json"] else {}
        decision = json.loads(row["decision_json"])
        recommendation = json.loads(row["recommendation_json"])
        ai_provenance = {
            "ai_receipt_fingerprint": row["ai_receipt_fingerprint"],
            "ai_interpretation_semantic_fingerprint": row["ai_interpretation_semantic_fingerprint"],
            "ai_provider": row["ai_provider"], "ai_model": row["ai_model"],
            "ai_correlation_id": row["ai_correlation_id"], "ai_request_fingerprint": row["ai_request_fingerprint"],
            "ai_status": row["ai_status"],
        }
        if ai_receipt_repository is not None and row["ai_receipt_fingerprint"] and row["ai_interpretation_semantic_fingerprint"]:
            receipt_for_semantic_check = ai_receipt_repository.get_receipt(row["ai_receipt_fingerprint"], workspace_id=row["workspace_id"])
            expected_ai_semantic = interpretation_semantic_fingerprint(receipt=receipt_for_semantic_check, workspace_id=row["workspace_id"])
            if expected_ai_semantic != row["ai_interpretation_semantic_fingerprint"]:
                raise ValueError("historical decision replay interpretation semantic mismatch")
        reconstructed = decision_semantic_fingerprint(
            policy_id=str(json.loads(row["recommendation_json"])["provenance"].get("learning_use_policy_id") or ""),
            workspace_id=row["workspace_id"],
            learning_use_mode=str(recommendation["provenance"].get("learning_use_mode") or ""),
            learning_context_id=row["learning_context_id"],
            learning_source_fingerprints=source_fps,
            ai_provenance=ai_provenance, interpretation=interpretation,
            decision=decision["payload"], recommendation=recommendation["payload"],
        )
        if reconstructed != row["decision_fingerprint"]:
            raise ValueError("historical decision semantic fingerprint mismatch")
        if workspace_id is not None and workspace_id != row["workspace_id"]:
            raise PermissionError("decision replay workspace mismatch")
        if ai_receipt_repository is not None and row["ai_receipt_fingerprint"]:
            receipt = ai_receipt_repository.get_receipt(row["ai_receipt_fingerprint"], workspace_id=row["workspace_id"])
            expected = {"provider": row["ai_provider"], "model": row["ai_model"], "correlation_id": row["ai_correlation_id"], "request_fingerprint": row["ai_request_fingerprint"], "status": row["ai_status"]}
            actual = {"provider": receipt.provider, "model": receipt.model, "correlation_id": receipt.correlation_id, "request_fingerprint": receipt.request_fingerprint, "status": receipt.status}
            if actual != expected:
                raise ValueError("historical decision replay AI receipt identity mismatch")
            if row["ai_interpretation_semantic_fingerprint"]:
                expected_semantic = interpretation_semantic_fingerprint(
                    receipt=receipt, workspace_id=row["workspace_id"]
                )
                if expected_semantic != row["ai_interpretation_semantic_fingerprint"]:
                    raise ValueError("historical decision replay interpretation semantic mismatch")
            if receipt.status != "succeeded":
                raise ValueError("historical decision replay requires the original successful AI receipt")
        if current_learning_context is not None:
            current_id = current_learning_context.get("context_id")
            current_fps = tuple(current_learning_context.get("source_fingerprints", []))
            if current_id != row["learning_context_id"] or current_fps != source_fps:
                raise ValueError("historical decision replay cannot use a different learning context")
        return DecisionReplaySnapshot(
            replay_id=row["replay_id"], workspace_id=row["workspace_id"],
            decision_fingerprint=row["decision_fingerprint"], learning_context_id=row["learning_context_id"],
            learning_source_fingerprints=source_fps, ai_receipt_fingerprint=row["ai_receipt_fingerprint"],
            ai_provider=row["ai_provider"], ai_model=row["ai_model"],
            ai_correlation_id=row["ai_correlation_id"], ai_request_fingerprint=row["ai_request_fingerprint"], ai_status=row["ai_status"],
            ai_interpretation_semantic_fingerprint=row["ai_interpretation_semantic_fingerprint"],
            decision=decision,
            recommendation=recommendation,
            interpretation=interpretation,
        )


__all__ = ["SCHEMA", "DecisionReplaySnapshot", "IntelligenceDecisionReplayRepository"]
