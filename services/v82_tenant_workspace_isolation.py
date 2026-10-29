"""Phase 8.2 — Tenant / Workspace Isolation Hardening.

A reusable fail-closed isolation boundary for durable KDO records.  This layer
proves that a resource belongs to exactly one tenant/workspace and that access
must carry the same scope.  It is an evidence/control boundary only.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, sqlite3


class IsolationViolation(ValueError):
    """Raised when tenant/workspace scope cannot be proven."""


@dataclass(frozen=True)
class IsolationBinding:
    resource_type: str
    resource_id: str
    tenant_id: str
    workspace_id: str
    bound_at: str
    fingerprint: str
    authority: str = "none"
    can_authorize: bool = False
    can_execute: bool = False
    can_call_provider: bool = False
    can_invoke_action_kernel: bool = False

    def __post_init__(self):
        if not all(str(x).strip() for x in (self.resource_type, self.resource_id, self.tenant_id, self.workspace_id)):
            raise ValueError("resource_type, resource_id, tenant_id and workspace_id are required")
        if len(self.fingerprint) != 64:
            raise ValueError("fingerprint must be sha256")
        if self.authority != "none" or any((self.can_authorize, self.can_execute, self.can_call_provider, self.can_invoke_action_kernel)):
            raise ValueError("isolation_boundary_cannot_grant_authority")


class TenantWorkspaceIsolation:
    VERSION = "v8.2"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS isolation_bindings (
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            bound_at TEXT NOT NULL, fingerprint TEXT NOT NULL,
            record_fingerprint TEXT NOT NULL,
            PRIMARY KEY(resource_type, resource_id),
            UNIQUE(tenant_id, workspace_id, resource_type, resource_id)
        )""")
        self._db.commit()

    def bind(self, resource_type: str, resource_id: str, tenant_id: str, workspace_id: str, *, bound_at: str | None = None) -> IsolationBinding:
        vals = tuple(str(x).strip() for x in (resource_type, resource_id, tenant_id, workspace_id))
        if not all(vals):
            raise IsolationViolation("scope_required")
        existing = self._db.execute("SELECT tenant_id,workspace_id FROM isolation_bindings WHERE resource_type=? AND resource_id=?", vals[:2]).fetchone()
        if existing and (existing["tenant_id"], existing["workspace_id"]) != vals[2:]:
            raise IsolationViolation("resource_scope_rebinding_denied")
        observed = bound_at or _now()
        fp = _sha({"version": self.VERSION, "resource_type": vals[0], "resource_id": vals[1], "tenant_id": vals[2], "workspace_id": vals[3], "bound_at": observed})
        self._db.execute("""INSERT INTO isolation_bindings
            (resource_type,resource_id,tenant_id,workspace_id,bound_at,fingerprint,record_fingerprint)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(resource_type,resource_id) DO UPDATE SET
            bound_at=excluded.bound_at,fingerprint=excluded.fingerprint,record_fingerprint=excluded.record_fingerprint""",
            (*vals, observed, fp, _sha({"resource_type": vals[0], "resource_id": vals[1], "tenant_id": vals[2], "workspace_id": vals[3], "bound_at": observed, "fingerprint": fp})))
        self._db.commit()
        return IsolationBinding(*vals, observed, fp)

    def assert_access(self, resource_type: str, resource_id: str, tenant_id: str, workspace_id: str) -> IsolationBinding:
        row = self._db.execute("SELECT * FROM isolation_bindings WHERE resource_type=? AND resource_id=?", (resource_type, resource_id)).fetchone()
        if not row:
            raise IsolationViolation("resource_scope_unknown")
        if not self.tamper_check(resource_type, resource_id):
            raise IsolationViolation("isolation_record_tampered")
        if row["tenant_id"] != tenant_id:
            raise IsolationViolation("tenant_mismatch")
        if row["workspace_id"] != workspace_id:
            raise IsolationViolation("workspace_mismatch")
        return IsolationBinding(row["resource_type"], row["resource_id"], row["tenant_id"], row["workspace_id"], row["bound_at"], row["fingerprint"])

    def list_scope(self, tenant_id: str, workspace_id: str) -> list[IsolationBinding]:
        rows = self._db.execute("SELECT * FROM isolation_bindings WHERE tenant_id=? AND workspace_id=? ORDER BY resource_type,resource_id", (tenant_id, workspace_id)).fetchall()
        return [IsolationBinding(r["resource_type"], r["resource_id"], r["tenant_id"], r["workspace_id"], r["bound_at"], r["fingerprint"]) for r in rows if self.tamper_check(r["resource_type"], r["resource_id"])]

    def tamper_check(self, resource_type: str, resource_id: str) -> bool:
        row = self._db.execute("SELECT * FROM isolation_bindings WHERE resource_type=? AND resource_id=?", (resource_type, resource_id)).fetchone()
        if not row: return False
        body = {"resource_type": row["resource_type"], "resource_id": row["resource_id"], "tenant_id": row["tenant_id"], "workspace_id": row["workspace_id"], "bound_at": row["bound_at"], "fingerprint": row["fingerprint"]}
        return _sha(body) == row["record_fingerprint"]

    def close(self): self._db.close()


def _now(): return datetime.now(timezone.utc).isoformat()
def _sha(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
