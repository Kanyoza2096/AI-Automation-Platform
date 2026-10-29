"""Phase 5.14 — canonical authorization replay proof.

Captures the exact canonical authorization evaluation performed for a governed
intelligence ActionIntent. Historical replay verifies the frozen authorization
record; it never substitutes today's RequestContext/permissions for history
and never grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.pipeline import run_action_pipeline
from core.request_context import RequestContext
from services.intelligence_action_intent_replay import IntelligenceActionIntentReplayRepository

SCHEMA = "KDO.IntelligenceAuthorizationReplay/v1"


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def authorization_context_snapshot(ctx: RequestContext) -> dict[str, Any]:
    return {
        "principal_id": ctx.principal_id,
        "workspace_id": ctx.workspace_id,
        "role": ctx.role,
        "permissions": sorted(str(p) for p in ctx.permissions),
        "species_privileges": bool(ctx.species_privileges),
        "auth_method": ctx.auth_method,
    }


def canonical_authorization_fingerprint(*, action_intent_fingerprint: str, context: dict[str, Any], pipeline: dict[str, Any]) -> str:
    return _fp({
        "schema_version": SCHEMA,
        "action_intent_fingerprint": action_intent_fingerprint,
        "authorization_context": context,
        "pipeline": pipeline,
    })


@dataclass(frozen=True)
class AuthorizationReplaySnapshot:
    replay_id: str
    workspace_id: str
    action_intent_fingerprint: str
    action_intent_replay_id: str
    authorization_context: dict[str, Any]
    pipeline: dict[str, Any]
    authorization_fingerprint: str
    schema_version: str = SCHEMA


class IntelligenceCanonicalAuthorizationReplayRepository:
    """Append-only proof of the canonical authorization evaluation."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE IF NOT EXISTS intelligence_authorization_replays (
            replay_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            action_intent_fingerprint TEXT NOT NULL UNIQUE,
            action_intent_replay_id TEXT NOT NULL,
            authorization_context_json TEXT NOT NULL,
            pipeline_json TEXT NOT NULL,
            authorization_fingerprint TEXT NOT NULL UNIQUE,
            schema_version TEXT NOT NULL
        )""")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def capture_from_snapshot(
        self,
        *,
        action_intent_snapshot: Any,
        request_context: RequestContext,
    ) -> AuthorizationReplaySnapshot:
        if request_context.workspace_id != action_intent_snapshot.workspace_id:
            raise PermissionError("canonical authorization workspace mismatch")
        action = action_intent_snapshot.action_intent
        action_type = str(action.get("payload", {}).get("action_type") or "")
        parameters = action.get("payload", {}).get("parameters", {})
        if not action_type:
            raise ValueError("ActionIntent action_type is required")
        if not isinstance(parameters, dict):
            raise ValueError("ActionIntent parameters must be a mapping")
        payload = {"action_type": action_type, **parameters}
        result = run_action_pipeline(
            action=action_type,
            payload=payload,
            permission="write",
            ctx=request_context,
            enqueue=False,
            domain_execute=None,
        )
        pipeline = result.to_dict()
        if not result.allowed:
            raise PermissionError(f"canonical authorization rejected ActionIntent: {result.reason}")
        # Keep only canonical authorization/decision evidence; outbox/job IDs are
        # excluded because this boundary is decision-only and must be replay-stable.
        pipeline = {
            "decision": pipeline["decision"],
            "allowed": bool(pipeline["allowed"]),
            "reason": pipeline["reason"],
            "decision_id": pipeline["decision_id"],
            "correlation_id": pipeline["correlation_id"],
            "workspace_id": pipeline["workspace_id"],
            "guardian": pipeline["guardian"],
            "metabolism": pipeline["metabolism"],
            "council": pipeline["council"],
            "director": pipeline["director"],
            "metadata": pipeline["metadata"],
        }
        context = authorization_context_snapshot(request_context)
        fp = canonical_authorization_fingerprint(
            action_intent_fingerprint=action_intent_snapshot.action_intent_fingerprint,
            context=context,
            pipeline=pipeline,
        )
        replay_id = "iar_" + _fp({"workspace_id": request_context.workspace_id, "action_intent_fingerprint": action_intent_snapshot.action_intent_fingerprint})[:24]
        snap = AuthorizationReplaySnapshot(
            replay_id=replay_id,
            workspace_id=request_context.workspace_id,
            action_intent_fingerprint=action_intent_snapshot.action_intent_fingerprint,
            action_intent_replay_id=action_intent_snapshot.replay_id,
            authorization_context=context,
            pipeline=pipeline,
            authorization_fingerprint=fp,
        )
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_authorization_replays
                (replay_id,workspace_id,action_intent_fingerprint,action_intent_replay_id,authorization_context_json,pipeline_json,authorization_fingerprint,schema_version)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(action_intent_fingerprint) DO NOTHING""",
                (snap.replay_id, snap.workspace_id, snap.action_intent_fingerprint, snap.action_intent_replay_id,
                 json.dumps(snap.authorization_context, sort_keys=True), json.dumps(snap.pipeline, sort_keys=True),
                 snap.authorization_fingerprint, SCHEMA),
            )
        return snap

    def replay(
        self,
        replay_id: str,
        *,
        action_intent_replay: IntelligenceActionIntentReplayRepository,
        action_intent_snapshot: Any,
        workspace_id: str | None = None,
    ) -> AuthorizationReplaySnapshot:
        row = self._conn.execute("SELECT * FROM intelligence_authorization_replays WHERE replay_id=?", (replay_id,)).fetchone()
        if row is None:
            raise KeyError(replay_id)
        if workspace_id is not None and workspace_id != row["workspace_id"]:
            raise PermissionError("canonical authorization replay workspace mismatch")
        if action_intent_snapshot.workspace_id != row["workspace_id"]:
            raise PermissionError("canonical authorization ActionIntent workspace mismatch")
        if action_intent_snapshot.action_intent_fingerprint != row["action_intent_fingerprint"]:
            raise ValueError("canonical authorization ActionIntent substitution detected")
        if action_intent_snapshot.replay_id != row["action_intent_replay_id"]:
            raise ValueError("canonical authorization ActionIntent replay identity mismatch")
        context = json.loads(row["authorization_context_json"])
        pipeline = json.loads(row["pipeline_json"])
        expected = canonical_authorization_fingerprint(
            action_intent_fingerprint=row["action_intent_fingerprint"], context=context, pipeline=pipeline
        )
        if expected != row["authorization_fingerprint"]:
            raise ValueError("stored canonical authorization fingerprint mismatch")
        if pipeline.get("workspace_id") != row["workspace_id"]:
            raise PermissionError("stored canonical authorization workspace mismatch")
        if pipeline.get("allowed") is not True:
            raise PermissionError("historical canonical authorization was not allowed")
        # Deliberately do NOT call run_action_pipeline here. Replaying history
        # must not be affected by current permissions, roles, guardian state,
        # metabolism, or DecisionGate configuration.
        return AuthorizationReplaySnapshot(
            replay_id=row["replay_id"], workspace_id=row["workspace_id"],
            action_intent_fingerprint=row["action_intent_fingerprint"], action_intent_replay_id=row["action_intent_replay_id"],
            authorization_context=context, pipeline=pipeline,
            authorization_fingerprint=row["authorization_fingerprint"], schema_version=row["schema_version"],
        )


__all__ = [
    "SCHEMA", "AuthorizationReplaySnapshot", "IntelligenceCanonicalAuthorizationReplayRepository",
    "authorization_context_snapshot", "canonical_authorization_fingerprint",
]
