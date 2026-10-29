"""Phase 5.15 — Authorization -> Action Kernel admission replay proof.

This boundary records the authoritative Action Kernel acceptance produced by the
existing intelligence handoff. Replay verifies the frozen admission evidence;
it never calls the kernel and never enqueues or executes an action.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib, json, sqlite3
from pathlib import Path
from typing import Any

SCHEMA = "KDO.IntelligenceActionKernelReplay/v1"

def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def kernel_admission_fingerprint(*, authorization_fingerprint: str, admission: dict[str, Any], kernel_result: dict[str, Any]) -> str:
    return _fp({"schema_version": SCHEMA, "authorization_fingerprint": authorization_fingerprint, "admission": admission, "kernel_result": kernel_result})

@dataclass(frozen=True)
class ActionKernelReplaySnapshot:
    replay_id: str
    workspace_id: str
    authorization_replay_id: str
    authorization_fingerprint: str
    action_intent_fingerprint: str
    kernel_intent_id: str
    admission: dict[str, Any]
    kernel_result: dict[str, Any]
    kernel_admission_fingerprint: str
    schema_version: str = SCHEMA

class IntelligenceActionKernelReplayRepository:
    """Append-only proof of canonical Action Kernel admission."""
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE IF NOT EXISTS intelligence_action_kernel_replays (
            replay_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            authorization_replay_id TEXT NOT NULL,
            authorization_fingerprint TEXT NOT NULL,
            action_intent_fingerprint TEXT NOT NULL UNIQUE,
            kernel_intent_id TEXT NOT NULL,
            admission_json TEXT NOT NULL,
            kernel_result_json TEXT NOT NULL,
            kernel_admission_fingerprint TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL
        )""")
        self._conn.commit()
    def close(self): self._conn.close()

    def capture(self, *, authorization_snapshot: Any, action_intent_snapshot: Any, handoff: Any, kernel_result: dict[str, Any] | None = None) -> ActionKernelReplaySnapshot:
        if authorization_snapshot.workspace_id != action_intent_snapshot.workspace_id:
            raise PermissionError("Action Kernel replay workspace mismatch")
        if handoff.workspace_id != authorization_snapshot.workspace_id:
            raise PermissionError("Action Kernel admission workspace mismatch")
        kernel_result = dict(kernel_result if kernel_result is not None else getattr(handoff, "kernel_result", {}) or {})
        if not kernel_result.get("intent_id"):
            raise ValueError("Action Kernel did not return an authoritative intent_id")
        admission = {
            "workspace_id": handoff.workspace_id,
            "action_type": handoff.action_type,
            "decision_id": handoff.decision_id,
            "idempotency_key": handoff.idempotency_key,
            "kernel_intent_id": handoff.kernel_intent_id,
            "action_intent_fingerprint": action_intent_snapshot.action_intent_fingerprint,
            "authorization_fingerprint": authorization_snapshot.authorization_fingerprint,
        }
        result = {k: kernel_result[k] for k in sorted(kernel_result) if k in {"ok","status","intent_id","action_type","idempotency_key","accepted"}}
        fp = kernel_admission_fingerprint(authorization_fingerprint=authorization_snapshot.authorization_fingerprint, admission=admission, kernel_result=result)
        replay_id = "ikr_" + _fp({"workspace_id": admission["workspace_id"], "action_intent_fingerprint": admission["action_intent_fingerprint"]})[:24]
        snap = ActionKernelReplaySnapshot(replay_id, admission["workspace_id"], authorization_snapshot.replay_id, authorization_snapshot.authorization_fingerprint, action_intent_snapshot.action_intent_fingerprint, str(kernel_result["intent_id"]), admission, result, fp)
        with self._conn:
            self._conn.execute("""INSERT INTO intelligence_action_kernel_replays
                (replay_id,workspace_id,authorization_replay_id,authorization_fingerprint,action_intent_fingerprint,kernel_intent_id,admission_json,kernel_result_json,kernel_admission_fingerprint,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(action_intent_fingerprint) DO NOTHING""",
                (snap.replay_id,snap.workspace_id,snap.authorization_replay_id,snap.authorization_fingerprint,snap.action_intent_fingerprint,snap.kernel_intent_id,json.dumps(snap.admission,sort_keys=True),json.dumps(snap.kernel_result,sort_keys=True),snap.kernel_admission_fingerprint,SCHEMA))
        return snap

    def replay(self, replay_id: str, *, authorization_snapshot: Any, action_intent_snapshot: Any, workspace_id: str | None = None) -> ActionKernelReplaySnapshot:
        row = self._conn.execute("SELECT * FROM intelligence_action_kernel_replays WHERE replay_id=?", (replay_id,)).fetchone()
        if row is None: raise KeyError(replay_id)
        if workspace_id is not None and workspace_id != row["workspace_id"]: raise PermissionError("Action Kernel replay workspace mismatch")
        if authorization_snapshot.workspace_id != row["workspace_id"] or action_intent_snapshot.workspace_id != row["workspace_id"]: raise PermissionError("Action Kernel replay workspace mismatch")
        if authorization_snapshot.replay_id != row["authorization_replay_id"]: raise ValueError("historical authorization replay identity mismatch")
        if authorization_snapshot.authorization_fingerprint != row["authorization_fingerprint"]: raise ValueError("historical authorization fingerprint mismatch")
        if action_intent_snapshot.action_intent_fingerprint != row["action_intent_fingerprint"]: raise ValueError("Action Kernel replay ActionIntent substitution detected")
        admission=json.loads(row["admission_json"]); result=json.loads(row["kernel_result_json"])
        expected=kernel_admission_fingerprint(authorization_fingerprint=row["authorization_fingerprint"], admission=admission, kernel_result=result)
        if expected != row["kernel_admission_fingerprint"]: raise ValueError("stored Action Kernel admission fingerprint mismatch")
        if admission.get("kernel_intent_id") != row["kernel_intent_id"] or result.get("intent_id") != row["kernel_intent_id"]: raise ValueError("Action Kernel intent identity mismatch")
        if admission.get("action_intent_fingerprint") != row["action_intent_fingerprint"]: raise ValueError("Action Kernel ActionIntent provenance mismatch")
        if admission.get("authorization_fingerprint") != row["authorization_fingerprint"]: raise ValueError("Action Kernel authorization provenance mismatch")
        if result.get("accepted") is not True and result.get("status") not in {"pending","approval_required","governance_required"}: raise ValueError("historical Action Kernel admission was not accepted")
        return ActionKernelReplaySnapshot(row["replay_id"],row["workspace_id"],row["authorization_replay_id"],row["authorization_fingerprint"],row["action_intent_fingerprint"],row["kernel_intent_id"],admission,result,row["kernel_admission_fingerprint"],row["schema_version"])

__all__=["SCHEMA","ActionKernelReplaySnapshot","IntelligenceActionKernelReplayRepository","kernel_admission_fingerprint"]
