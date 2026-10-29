"""Phase 8.1 — Security Control Plane.

Durable, tenant/workspace-scoped security control posture. This module is
an evidence/control surface only: it does not authorize, execute, call
providers, invoke the Action Kernel, or mutate external systems.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable


class ControlState(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    QUARANTINED = "quarantined"


class PostureState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    SECURITY_HOLD = "security_hold"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SecurityControl:
    control_id: str
    workspace_id: str
    name: str
    state: ControlState
    version: str
    evidence_fingerprint: str
    observed_at: str
    authority: str = "none"
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False
    can_invoke_action_kernel: bool = False

    def __post_init__(self) -> None:
        if not self.control_id.strip() or not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("control_id, workspace_id and name are required")
        if len(self.evidence_fingerprint) != 64:
            raise ValueError("evidence_fingerprint must be sha256")
        if self.authority != "none" or any((self.can_authorize, self.can_execute, self.can_call_provider, self.can_invoke_action_kernel)):
            raise ValueError("security_control_cannot_grant_authority")


@dataclass(frozen=True)
class SecurityPosture:
    workspace_id: str
    state: PostureState
    control_count: int
    healthy_controls: int
    degraded_controls: int
    unknown_controls: int
    quarantined_controls: int
    posture_fingerprint: str
    observed_at: str
    authority: str = "none"
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False
    can_invoke_action_kernel: bool = False

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if len(self.posture_fingerprint) != 64:
            raise ValueError("posture_fingerprint must be sha256")
        if self.authority != "none" or any((self.can_authorize, self.can_execute, self.can_call_provider, self.can_invoke_action_kernel)):
            raise ValueError("security_posture_cannot_grant_authority")


class SecurityControlPlane:
    VERSION = "v8.1"

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS security_controls (
            control_id TEXT NOT NULL, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
            state TEXT NOT NULL, version TEXT NOT NULL, evidence_fingerprint TEXT NOT NULL,
            observed_at TEXT NOT NULL, record_fingerprint TEXT NOT NULL,
            PRIMARY KEY (workspace_id, control_id)
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS security_postures (
            workspace_id TEXT PRIMARY KEY, state TEXT NOT NULL, control_count INTEGER NOT NULL,
            healthy_controls INTEGER NOT NULL, degraded_controls INTEGER NOT NULL,
            unknown_controls INTEGER NOT NULL, quarantined_controls INTEGER NOT NULL,
            posture_fingerprint TEXT NOT NULL, observed_at TEXT NOT NULL
        )""")
        self._db.commit()

    def upsert_control(self, control: SecurityControl) -> SecurityControl:
        record = {
            "control_id": control.control_id, "workspace_id": control.workspace_id,
            "name": control.name, "state": control.state.value, "version": control.version,
            "evidence_fingerprint": control.evidence_fingerprint, "observed_at": control.observed_at,
        }
        fp = _sha(record)
        self._db.execute("""INSERT INTO security_controls
            (control_id,workspace_id,name,state,version,evidence_fingerprint,observed_at,record_fingerprint)
            VALUES (:control_id,:workspace_id,:name,:state,:version,:evidence_fingerprint,:observed_at,:fp)
            ON CONFLICT(workspace_id,control_id) DO UPDATE SET
              name=excluded.name,state=excluded.state,version=excluded.version,
              evidence_fingerprint=excluded.evidence_fingerprint,observed_at=excluded.observed_at,
              record_fingerprint=excluded.record_fingerprint""", {**record, "fp": fp})
        self._db.commit()
        return control

    def get_control(self, workspace_id: str, control_id: str) -> SecurityControl | None:
        row = self._db.execute("SELECT * FROM security_controls WHERE workspace_id=? AND control_id=?", (workspace_id, control_id)).fetchone()
        return _control_from_row(row) if row else None

    def evaluate(self, workspace_id: str) -> SecurityPosture:
        rows = self._db.execute("SELECT * FROM security_controls WHERE workspace_id=? ORDER BY control_id", (workspace_id,)).fetchall()
        if not rows:
            state = PostureState.UNKNOWN
        else:
            states = [r["state"] for r in rows]
            if "quarantined" in states:
                state = PostureState.SECURITY_HOLD
            elif "unknown" in states:
                state = PostureState.UNKNOWN
            elif "degraded" in states or "disabled" in states:
                state = PostureState.DEGRADED
            else:
                state = PostureState.HEALTHY
        counts = {s.value: sum(r["state"] == s.value for r in rows) for s in ControlState}
        payload = {
            "version": self.VERSION, "workspace_id": workspace_id, "state": state.value,
            "controls": [{"control_id": r["control_id"], "state": r["state"], "version": r["version"],
                           "evidence_fingerprint": r["evidence_fingerprint"], "record_fingerprint": r["record_fingerprint"]} for r in rows],
        }
        fp = _sha(payload)
        observed = _now()
        self._db.execute("""INSERT INTO security_postures
            (workspace_id,state,control_count,healthy_controls,degraded_controls,unknown_controls,quarantined_controls,posture_fingerprint,observed_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(workspace_id) DO UPDATE SET state=excluded.state,control_count=excluded.control_count,
              healthy_controls=excluded.healthy_controls,degraded_controls=excluded.degraded_controls,
              unknown_controls=excluded.unknown_controls,quarantined_controls=excluded.quarantined_controls,
              posture_fingerprint=excluded.posture_fingerprint,observed_at=excluded.observed_at""",
            (workspace_id, state.value, len(rows), counts["enabled"], counts["degraded"] + counts["disabled"],
             counts["unknown"], counts["quarantined"], fp, observed))
        self._db.commit()
        return SecurityPosture(workspace_id, state, len(rows), counts["enabled"], counts["degraded"] + counts["disabled"],
                               counts["unknown"], counts["quarantined"], fp, observed)

    def tamper_check(self, workspace_id: str, control_id: str) -> bool:
        row = self._db.execute("SELECT * FROM security_controls WHERE workspace_id=? AND control_id=?", (workspace_id, control_id)).fetchone()
        if not row:
            return False
        record = {"control_id": row["control_id"], "workspace_id": row["workspace_id"], "name": row["name"],
                  "state": row["state"], "version": row["version"], "evidence_fingerprint": row["evidence_fingerprint"],
                  "observed_at": row["observed_at"]}
        return _sha(record) == row["record_fingerprint"]

    def close(self) -> None:
        self._db.close()


def _control_from_row(row: sqlite3.Row) -> SecurityControl:
    return SecurityControl(row["control_id"], row["workspace_id"], row["name"], ControlState(row["state"]),
                           row["version"], row["evidence_fingerprint"], row["observed_at"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_fingerprint(*parts: str) -> str:
    return _sha(list(parts))
