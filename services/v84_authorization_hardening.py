"""Phase 8.4 — Canonical Authorization Hardening.

Harden the existing canonical authorization path without creating a second
execution authority. Identity/session provenance is validated first, then the
existing KDO action pipeline is invoked for the authorization decision. The
result is durably recorded as decision evidence; this layer never executes an
ActionIntent, calls a provider, or invokes the Action Kernel directly.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, sqlite3
from typing import Any

from core.pipeline import run_action_pipeline
from services.v83_identity_access_hardening import IdentityAccessHardening, IdentityAccessViolation

SCHEMA = "KDO.Phase8.4.AuthorizationHardening/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class AuthorizationHardeningViolation(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorizationDecision:
    decision_id: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    action_type: str
    allowed: bool
    reason: str
    pipeline: dict[str, Any]
    decision_fingerprint: str
    observed_at: str
    schema_version: str = SCHEMA


class CanonicalAuthorizationHardening:
    VERSION = "v8.4"

    def __init__(self, identity_access: IdentityAccessHardening, db_path: str = ":memory:") -> None:
        self.identity_access = identity_access
        self.db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS authorization_decisions (
            decision_id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            allowed INTEGER NOT NULL,
            reason TEXT NOT NULL,
            pipeline_json TEXT NOT NULL,
            decision_fingerprint TEXT NOT NULL UNIQUE,
            observed_at TEXT NOT NULL,
            schema_version TEXT NOT NULL
        )""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def authorize(self, *, session_id: str, tenant_id: str, workspace_id: str,
                  action_type: str, payload: dict[str, Any], permission: str = "write",
                  now: str | None = None) -> AuthorizationDecision:
        if not str(action_type).strip() or not isinstance(payload, dict):
            raise AuthorizationHardeningViolation("invalid_authorization_request")
        try:
            session = self.identity_access.validate_session(session_id, tenant_id, workspace_id, now=now)
        except IdentityAccessViolation as exc:
            raise AuthorizationHardeningViolation(str(exc)) from exc
        ctx = self._request_context(session, permission)
        result = run_action_pipeline(
            action=action_type,
            payload={"action_type": action_type, **payload},
            permission=permission,
            ctx=ctx,
            enqueue=False,
            domain_execute=None,
        )
        pipeline = result.to_dict()
        # Decision-only evidence: no outbox/job/provider execution data is admitted.
        pipeline = {k: pipeline.get(k) for k in (
            "decision", "allowed", "reason", "decision_id", "correlation_id",
            "workspace_id", "guardian", "metabolism", "council", "director", "metadata"
        )}
        if pipeline.get("workspace_id") != workspace_id:
            raise AuthorizationHardeningViolation("authorization_workspace_mismatch")
        metadata = dict(pipeline.get("metadata") or {})
        metadata["phase8_4_fingerprint_input"] = session_id
        pipeline["metadata"] = metadata
        body = {
            "schema_version": SCHEMA,
            "principal_id": session.principal_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "action_type": action_type,
            "allowed": bool(pipeline.get("allowed")),
            "reason": str(pipeline.get("reason") or ""),
            "pipeline": pipeline,
            "session_id": session_id,
        }
        observed = now or _now()
        fp = _fp(body)
        decision_id = "auth8_4_" + fp[:24]
        decision = AuthorizationDecision(
            decision_id=decision_id,
            principal_id=session.principal_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_type=action_type,
            allowed=bool(pipeline.get("allowed")),
            reason=str(pipeline.get("reason") or ""),
            pipeline=pipeline,
            decision_fingerprint=fp,
            observed_at=observed,
        )
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO authorization_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, session.principal_id, tenant_id, workspace_id, action_type,
                 int(decision.allowed), decision.reason, json.dumps(pipeline, sort_keys=True),
                 fp, observed, SCHEMA),
            )
        return decision

    def get(self, decision_id: str, *, tenant_id: str, workspace_id: str) -> AuthorizationDecision:
        row = self._db.execute("SELECT * FROM authorization_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            raise AuthorizationHardeningViolation("authorization_decision_unknown")
        if row["tenant_id"] != tenant_id:
            raise AuthorizationHardeningViolation("authorization_tenant_mismatch")
        if row["workspace_id"] != workspace_id:
            raise AuthorizationHardeningViolation("authorization_workspace_mismatch")
        pipeline = json.loads(row["pipeline_json"])
        expected = _fp({
            "schema_version": SCHEMA, "principal_id": row["principal_id"], "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"], "action_type": row["action_type"],
            "allowed": bool(row["allowed"]), "reason": row["reason"], "pipeline": pipeline,
            "session_id": "<redacted-replay-input>"
        })
        # The persisted fingerprint includes the original session identity, so
        # retrieval verifies the record structurally via the dedicated helper below.
        # A decision is never trusted merely because its row exists.
        if not self._verify_record(row):
            raise AuthorizationHardeningViolation("authorization_decision_tampered")
        return AuthorizationDecision(row["decision_id"], row["principal_id"], row["tenant_id"], row["workspace_id"], row["action_type"], bool(row["allowed"]), row["reason"], pipeline, row["decision_fingerprint"], row["observed_at"], row["schema_version"])

    def _verify_record(self, row: sqlite3.Row) -> bool:
        # session identity is deliberately not persisted as a separate column;
        # the decision fingerprint is verified against a stable record projection
        # whose exact value is also stored in metadata.
        pipeline = json.loads(row["pipeline_json"])
        marker = pipeline.get("metadata", {}).get("phase8_4_fingerprint_input") if isinstance(pipeline.get("metadata"), dict) else None
        if marker is None:
            # Legacy/foreign pipeline evidence is not accepted as hardened evidence.
            return False
        body = {
            "schema_version": SCHEMA,
            "principal_id": row["principal_id"], "tenant_id": row["tenant_id"], "workspace_id": row["workspace_id"],
            "action_type": row["action_type"], "allowed": bool(row["allowed"]), "reason": row["reason"],
            "pipeline": pipeline, "session_id": marker,
        }
        return _fp(body) == row["decision_fingerprint"]

    @staticmethod
    def _request_context(session, permission: str):
        from core.request_context import RequestContext
        return RequestContext(
            principal_id=session.principal_id,
            workspace_id=session.workspace_id,
            role="authenticated",
            permissions=frozenset({permission}),
            species_privileges=False,
            auth_method=session.assurance_level,
        )


__all__ = ["SCHEMA", "AuthorizationHardeningViolation", "AuthorizationDecision", "CanonicalAuthorizationHardening"]
