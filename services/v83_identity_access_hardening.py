"""Phase 8.3 — Identity & Access Hardening.

Durable identity/session evidence boundary.  This layer establishes who a
requesting principal is, the tenant/workspace scope they are bound to, the
authentication assurance and whether a session is currently valid.  It does
not decide authorization or execute consequential operations.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import hashlib, json, sqlite3


class IdentityState(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class SessionState(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class IdentityAccessViolation(ValueError):
    """Raised when identity/session provenance cannot be proven."""


@dataclass(frozen=True)
class PrincipalIdentity:
    principal_id: str
    tenant_id: str
    workspace_id: str
    state: IdentityState
    assurance_level: str
    roles_fingerprint: str
    capabilities_fingerprint: str
    version: str
    observed_at: str
    record_fingerprint: str
    authority: str = "none"
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False
    can_invoke_action_kernel: bool = False

    def __post_init__(self):
        if not all(str(x).strip() for x in (self.principal_id, self.tenant_id, self.workspace_id, self.assurance_level, self.version)):
            raise ValueError("identity_scope_and_assurance_required")
        for name, value in (("roles_fingerprint", self.roles_fingerprint), ("capabilities_fingerprint", self.capabilities_fingerprint), ("record_fingerprint", self.record_fingerprint)):
            if len(value) != 64:
                raise ValueError(f"{name}_must_be_sha256")
        if self.authority != "none" or any((self.can_authorize, self.can_execute, self.can_call_provider, self.can_invoke_action_kernel)):
            raise ValueError("identity_layer_cannot_grant_authority")


@dataclass(frozen=True)
class AccessSession:
    session_id: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    state: SessionState
    assurance_level: str
    issued_at: str
    expires_at: str
    observed_at: str
    record_fingerprint: str
    authority: str = "none"
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False
    can_invoke_action_kernel: bool = False

    def __post_init__(self):
        if not all(str(x).strip() for x in (self.session_id, self.principal_id, self.tenant_id, self.workspace_id, self.assurance_level, self.issued_at, self.expires_at)):
            raise ValueError("session_identity_and_scope_required")
        if len(self.record_fingerprint) != 64:
            raise ValueError("record_fingerprint_must_be_sha256")
        if self.authority != "none" or any((self.can_authorize, self.can_execute, self.can_call_provider, self.can_invoke_action_kernel)):
            raise ValueError("session_layer_cannot_grant_authority")


class IdentityAccessHardening:
    VERSION = "v8.3"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS identities (
            principal_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            state TEXT NOT NULL, assurance_level TEXT NOT NULL, roles_fingerprint TEXT NOT NULL,
            capabilities_fingerprint TEXT NOT NULL, version TEXT NOT NULL, observed_at TEXT NOT NULL,
            record_fingerprint TEXT NOT NULL
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS access_sessions (
            session_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL, state TEXT NOT NULL, assurance_level TEXT NOT NULL,
            version TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, observed_at TEXT NOT NULL,
            record_fingerprint TEXT NOT NULL
        )""")
        self._db.commit()

    def register_identity(self, principal_id: str, tenant_id: str, workspace_id: str, *, assurance_level: str = "standard", roles_fingerprint: str | None = None, capabilities_fingerprint: str | None = None, state: IdentityState = IdentityState.ACTIVE, observed_at: str | None = None) -> PrincipalIdentity:
        vals = [str(x).strip() for x in (principal_id, tenant_id, workspace_id, assurance_level)]
        if not all(vals): raise IdentityAccessViolation("identity_scope_required")
        old = self._db.execute("SELECT tenant_id,workspace_id FROM identities WHERE principal_id=?", (vals[0],)).fetchone()
        if old and (old["tenant_id"], old["workspace_id"]) != (vals[1], vals[2]):
            raise IdentityAccessViolation("identity_scope_rebinding_denied")
        observed = observed_at or _now()
        rf = roles_fingerprint or _sha([])
        cf = capabilities_fingerprint or _sha([])
        body = dict(version=self.VERSION, principal_id=vals[0], tenant_id=vals[1], workspace_id=vals[2], state=state.value, assurance_level=vals[3], roles_fingerprint=rf, capabilities_fingerprint=cf, observed_at=observed)
        fp = _sha(body)
        self._db.execute("""INSERT INTO identities VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(principal_id) DO UPDATE SET state=excluded.state, assurance_level=excluded.assurance_level,
            roles_fingerprint=excluded.roles_fingerprint, capabilities_fingerprint=excluded.capabilities_fingerprint,
            version=excluded.version, observed_at=excluded.observed_at, record_fingerprint=excluded.record_fingerprint""",
            (*[vals[0], vals[1], vals[2], state.value, vals[3], rf, cf, self.VERSION, observed, fp],))
        self._db.commit()
        return PrincipalIdentity(vals[0], vals[1], vals[2], state, vals[3], rf, cf, self.VERSION, observed, fp)

    def get_identity(self, principal_id: str) -> PrincipalIdentity | None:
        row = self._db.execute("SELECT * FROM identities WHERE principal_id=?", (principal_id,)).fetchone()
        return _identity(row) if row else None

    def open_session(self, session_id: str, principal_id: str, tenant_id: str, workspace_id: str, expires_at: str, *, issued_at: str | None = None) -> AccessSession:
        identity = self.get_identity(principal_id)
        if not identity or not self.tamper_check_identity(principal_id): raise IdentityAccessViolation("identity_unproven")
        if identity.tenant_id != tenant_id or identity.workspace_id != workspace_id: raise IdentityAccessViolation("identity_scope_mismatch")
        if identity.state != IdentityState.ACTIVE: raise IdentityAccessViolation("identity_not_active")
        issued = issued_at or _now()
        if _parse(expires_at) <= _parse(issued): raise IdentityAccessViolation("session_expiry_invalid")
        body = dict(version=self.VERSION, session_id=session_id, principal_id=principal_id, tenant_id=tenant_id, workspace_id=workspace_id, state=SessionState.ACTIVE.value, assurance_level=identity.assurance_level, issued_at=issued, expires_at=expires_at, observed_at=issued)
        fp = _sha(body)
        self._db.execute("INSERT INTO access_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (session_id, principal_id, tenant_id, workspace_id, SessionState.ACTIVE.value, identity.assurance_level, self.VERSION, issued, expires_at, issued, fp))
        self._db.commit()
        return AccessSession(session_id, principal_id, tenant_id, workspace_id, SessionState.ACTIVE, identity.assurance_level, issued, expires_at, issued, fp)

    def validate_session(self, session_id: str, tenant_id: str, workspace_id: str, *, now: str | None = None) -> AccessSession:
        row = self._db.execute("SELECT * FROM access_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row: raise IdentityAccessViolation("session_unknown")
        if not self.tamper_check_session(session_id): raise IdentityAccessViolation("session_tampered")
        if row["tenant_id"] != tenant_id: raise IdentityAccessViolation("session_tenant_mismatch")
        if row["workspace_id"] != workspace_id: raise IdentityAccessViolation("session_workspace_mismatch")
        identity = self.get_identity(row["principal_id"])
        if not identity or not self.tamper_check_identity(row["principal_id"]): raise IdentityAccessViolation("identity_unproven")
        if identity.state != IdentityState.ACTIVE: raise IdentityAccessViolation("identity_not_active")
        current = _parse(now or _now())
        state = SessionState(row["state"])
        if state == SessionState.ACTIVE and current >= _parse(row["expires_at"]):
            state = SessionState.EXPIRED
            observed = now or _now()
            self._set_session_state(session_id, state.value, observed)
        if state != SessionState.ACTIVE: raise IdentityAccessViolation(f"session_{state.value}")
        return _session(row, state)

    def revoke_session(self, session_id: str) -> None:
        if not self.tamper_check_session(session_id): raise IdentityAccessViolation("session_unproven")
        self._set_session_state(session_id, SessionState.REVOKED.value, _now())

    def _set_session_state(self, session_id: str, state: str, observed_at: str) -> None:
        row = self._db.execute("SELECT * FROM access_sessions WHERE session_id=?", (session_id,)).fetchone()
        body = {k: row[k] for k in ("version","session_id","principal_id","tenant_id","workspace_id","assurance_level","issued_at","expires_at")}
        body.update(state=state, observed_at=observed_at)
        self._db.execute("UPDATE access_sessions SET state=?, observed_at=?, record_fingerprint=? WHERE session_id=?", (state, observed_at, _sha(body), session_id))
        self._db.commit()

    def tamper_check_identity(self, principal_id: str) -> bool:
        row = self._db.execute("SELECT * FROM identities WHERE principal_id=?", (principal_id,)).fetchone()
        if not row: return False
        body = {k: row[k] for k in ("version","principal_id","tenant_id","workspace_id","state","assurance_level","roles_fingerprint","capabilities_fingerprint","observed_at")}
        return _sha(body) == row["record_fingerprint"]

    def tamper_check_session(self, session_id: str) -> bool:
        row = self._db.execute("SELECT * FROM access_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row: return False
        body = {k: row[k] for k in ("version","session_id","principal_id","tenant_id","workspace_id","state","assurance_level","issued_at","expires_at","observed_at")}
        return _sha(body) == row["record_fingerprint"]

    def close(self): self._db.close()


def _identity(row):
    return PrincipalIdentity(row["principal_id"], row["tenant_id"], row["workspace_id"], IdentityState(row["state"]), row["assurance_level"], row["roles_fingerprint"], row["capabilities_fingerprint"], row["version"], row["observed_at"], row["record_fingerprint"])

def _session(row, state=None):
    return AccessSession(row["session_id"], row["principal_id"], row["tenant_id"], row["workspace_id"], state or SessionState(row["state"]), row["assurance_level"], row["issued_at"], row["expires_at"], row["observed_at"], row["record_fingerprint"])

def _now(): return datetime.now(timezone.utc).isoformat()
def _parse(v): return datetime.fromisoformat(v.replace("Z", "+00:00"))
def _sha(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
