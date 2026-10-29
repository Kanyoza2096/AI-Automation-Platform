"""Phase 5.3 — bounded learning feedback from authoritative outcomes.

Learning is a downstream, append-only evidence plane. It may record what was
observed and what was measured after a governed execution, but it cannot mutate
historical truth, governance, authorization, policy, Action Kernel state, or
provider state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

LEARNING_SCHEMA = "KDO.IntelligenceLearningFeedback/v1"
LEARNING_STATES = {"positive", "negative", "uncertain", "insufficient_evidence"}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class IntelligenceLearningFeedback:
    workspace_id: str
    feedback_id: str
    execution_id: str
    outcome_id: str
    attribution_fingerprint: str
    learning_state: str
    measured_success: bool | None
    confidence: float
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LEARNING_SCHEMA,
            "workspace_id": self.workspace_id,
            "feedback_id": self.feedback_id,
            "execution_id": self.execution_id,
            "outcome_id": self.outcome_id,
            "attribution_fingerprint": self.attribution_fingerprint,
            "learning_state": self.learning_state,
            "measured_success": self.measured_success,
            "confidence": self.confidence,
            "evidence_fingerprint": self.evidence_fingerprint,
            "authority": "none",
            "causal_claim": False,
            "governance_mutation": False,
            "execution_authority": False,
        }


class IntelligenceLearningFeedbackStore:
    """Append-only, restart-safe storage for learning feedback."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS intelligence_learning_feedback (
                feedback_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                outcome_id TEXT NOT NULL,
                attribution_fingerprint TEXT NOT NULL,
                learning_state TEXT NOT NULL,
                measured_success INTEGER,
                confidence REAL NOT NULL,
                evidence_json TEXT NOT NULL,
                evidence_fingerprint TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_learning_feedback_workspace
              ON intelligence_learning_feedback(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_learning_feedback_execution
              ON intelligence_learning_feedback(execution_id);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append(self, feedback: IntelligenceLearningFeedback) -> None:
        validate_learning_feedback(feedback.to_dict())
        row = feedback.to_dict()
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_learning_feedback
                (feedback_id,workspace_id,execution_id,outcome_id,attribution_fingerprint,
                 learning_state,measured_success,confidence,evidence_json,evidence_fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(feedback_id) DO NOTHING""",
                (
                    row["feedback_id"], row["workspace_id"], row["execution_id"], row["outcome_id"],
                    row["attribution_fingerprint"], row["learning_state"],
                    None if row["measured_success"] is None else int(row["measured_success"]),
                    row["confidence"], json.dumps(row, sort_keys=True), row["evidence_fingerprint"],
                ),
            )

    def load_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT evidence_json FROM intelligence_learning_feedback WHERE workspace_id=? ORDER BY feedback_id",
            (workspace_id,),
        ).fetchall()
        return [json.loads(row["evidence_json"]) for row in rows]


class IntelligenceLearningFeedbackEngine:
    """Derive advisory learning evidence only from authoritative attribution."""

    VERSION = "phase5.3"

    def derive(
        self,
        *,
        workspace_id: str,
        attribution: Mapping[str, Any],
        outcome_measurement: Mapping[str, Any],
    ) -> IntelligenceLearningFeedback:
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")
        if not isinstance(attribution, Mapping) or not isinstance(outcome_measurement, Mapping):
            raise ValueError("attribution and outcome_measurement must be mappings")
        if str(attribution.get("workspace_id") or "") != ws:
            raise PermissionError("attribution workspace mismatch")
        if str(outcome_measurement.get("workspace_id") or ws) != ws:
            raise PermissionError("outcome measurement workspace mismatch")
        if attribution.get("authoritative_outcome_source") != "verification_and_reconciliation":
            raise ValueError("learning requires authoritative verification and reconciliation attribution")
        if attribution.get("causal_claim") is not False or attribution.get("action_authority_granted") is not False:
            raise ValueError("learning attribution must remain non-causal and non-authoritative")

        execution_id = str(attribution.get("execution_id") or "").strip()
        outcome_id = str(attribution.get("outcome_id") or "").strip()
        attribution_fp = str(attribution.get("evidence_fingerprint") or "").strip()
        if not execution_id or not outcome_id or not attribution_fp:
            raise ValueError("complete attribution evidence is required")

        measured = outcome_measurement.get("measured_success")
        if measured is not None and not isinstance(measured, bool):
            raise ValueError("measured_success must be boolean or null")
        confidence = float(outcome_measurement.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        if measured is None or confidence < 0.5:
            state = "uncertain"
        else:
            state = "positive" if measured else "negative"

        material = {
            "version": self.VERSION,
            "workspace_id": ws,
            "execution_id": execution_id,
            "outcome_id": outcome_id,
            "attribution_fingerprint": attribution_fp,
            "learning_state": state,
            "measured_success": measured,
            "confidence": confidence,
        }
        evidence_fp = _fingerprint(material)
        feedback_id = "ilf_" + evidence_fp[:24]
        feedback = IntelligenceLearningFeedback(
            workspace_id=ws,
            feedback_id=feedback_id,
            execution_id=execution_id,
            outcome_id=outcome_id,
            attribution_fingerprint=attribution_fp,
            learning_state=state,
            measured_success=measured,
            confidence=confidence,
            evidence_fingerprint=evidence_fp,
        )
        validate_learning_feedback(feedback.to_dict())
        return feedback


def validate_learning_feedback(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != LEARNING_SCHEMA:
        raise ValueError("unsupported learning feedback schema")
    for key in ("workspace_id", "feedback_id", "execution_id", "outcome_id", "attribution_fingerprint", "evidence_fingerprint"):
        if not str(value.get(key) or "").strip():
            raise ValueError(f"{key} is required")
    if value.get("learning_state") not in LEARNING_STATES:
        raise ValueError("invalid learning_state")
    confidence = float(value.get("confidence", -1))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("learning feedback cannot grant authority")
    if value.get("causal_claim") is not False:
        raise ValueError("learning feedback cannot claim causation")
    if value.get("governance_mutation") is not False or value.get("execution_authority") is not False:
        raise ValueError("learning feedback cannot mutate governance or execute")


__all__ = [
    "LEARNING_SCHEMA",
    "IntelligenceLearningFeedback",
    "IntelligenceLearningFeedbackEngine",
    "IntelligenceLearningFeedbackStore",
    "validate_learning_feedback",
]
