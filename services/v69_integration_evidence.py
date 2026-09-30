"""V69.13 — tenant-scoped integration observability and audit evidence.

Evidence is descriptive, not authoritative.  It records what the control plane
observed/decided, never grants authority, stores secrets, or executes providers.
The production persistence contract is append-only and tenant-scoped; the
in-memory store is deterministic for unit tests and offline certification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Protocol


class EvidenceError(ValueError):
    pass


_SECRET_KEYS = {
    "secret", "token", "access_token", "refresh_token", "api_key", "apikey",
    "authorization", "credential", "credentials", "password", "client_secret",
    "private_key", "signature", "raw_secret", "secret_ref_value",
}


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<depth-limited>"
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            k = str(key)
            out[k] = "<redacted>" if k.lower() in _SECRET_KEYS else _safe(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_safe(v, depth=depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def payload_fingerprint(payload: Mapping[str, Any] | None) -> str:
    material = json.dumps(_safe(payload or {}), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class IntegrationEvidence:
    evidence_id: str
    workspace_id: str
    connection_id: str
    connector_key: str
    event_type: str
    occurred_at: datetime
    outcome: str
    reason_codes: tuple[str, ...] = ()
    action: str = ""
    correlation_id: str | None = None
    actor: str = "system"
    provider: str = ""
    connector_version: str = ""
    payload_fingerprint: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    previous_hash: str = ""
    evidence_hash: str = ""
    authority_granted: bool = field(default=False, init=False)
    execution_effect: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connection_id.strip() or not self.connector_key.strip():
            raise EvidenceError("workspace, connection and connector are required")
        if self.occurred_at.tzinfo is None:
            raise EvidenceError("occurred_at must be timezone-aware")
        if self.authority_granted or self.execution_effect != "none":
            raise EvidenceError("evidence cannot grant authority or execute effects")
        if not self.evidence_hash:
            object.__setattr__(self, "evidence_hash", compute_evidence_hash(self))


class EvidenceSink(Protocol):
    def append(self, evidence: IntegrationEvidence) -> IntegrationEvidence: ...


class MemoryEvidenceStore:
    """Append-only tenant-scoped evidence store for tests and local operation."""
    def __init__(self) -> None:
        self._events: list[IntegrationEvidence] = []
        self._ids: set[str] = set()
        self._last_hash: dict[tuple[str, str], str] = {}

    def append(self, evidence: IntegrationEvidence) -> IntegrationEvidence:
        key = (evidence.workspace_id, evidence.connection_id)
        if evidence.evidence_id in self._ids:
            raise EvidenceError("duplicate_evidence_id")
        expected_previous = self._last_hash.get(key, "")
        if evidence.previous_hash != expected_previous:
            raise EvidenceError("evidence_chain_mismatch")
        expected_hash = compute_evidence_hash(evidence)
        if evidence.evidence_hash != expected_hash:
            raise EvidenceError("evidence_hash_mismatch")
        self._events.append(evidence)
        self._ids.add(evidence.evidence_id)
        self._last_hash[key] = evidence.evidence_hash
        return evidence

    def list(self, workspace_id: str, connection_id: str | None = None) -> tuple[IntegrationEvidence, ...]:
        return tuple(e for e in self._events if e.workspace_id == workspace_id and (connection_id is None or e.connection_id == connection_id))

    def last_hash(self, workspace_id: str, connection_id: str) -> str:
        return self._last_hash.get((workspace_id, connection_id), "")


def compute_evidence_hash(evidence: IntegrationEvidence) -> str:
    material = {
        "evidence_id": evidence.evidence_id,
        "workspace_id": evidence.workspace_id,
        "connection_id": evidence.connection_id,
        "connector_key": evidence.connector_key,
        "event_type": evidence.event_type,
        "occurred_at": evidence.occurred_at.astimezone(timezone.utc).isoformat(),
        "outcome": evidence.outcome,
        "reason_codes": list(evidence.reason_codes),
        "action": evidence.action,
        "correlation_id": evidence.correlation_id,
        "actor": evidence.actor,
        "provider": evidence.provider,
        "connector_version": evidence.connector_version,
        "payload_fingerprint": evidence.payload_fingerprint,
        "metadata": _safe(evidence.metadata),
        "previous_hash": evidence.previous_hash,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def emit(
    sink: EvidenceSink,
    *,
    evidence_id: str,
    workspace_id: str,
    connection_id: str,
    connector_key: str,
    event_type: str,
    outcome: str,
    occurred_at: datetime | None = None,
    reason_codes: Iterable[str] = (),
    action: str = "",
    correlation_id: str | None = None,
    actor: str = "system",
    provider: str = "",
    connector_version: str = "",
    payload: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> IntegrationEvidence:
    """Build and append one evidence record. Payloads are fingerprinted/redacted."""
    if not evidence_id.strip():
        raise EvidenceError("evidence_id_required")
    occurred_at = occurred_at or datetime.now(timezone.utc)
    previous = sink.last_hash(workspace_id, connection_id) if isinstance(sink, MemoryEvidenceStore) else ""
    evidence = IntegrationEvidence(
        evidence_id=evidence_id,
        workspace_id=workspace_id,
        connection_id=connection_id,
        connector_key=connector_key,
        event_type=event_type,
        occurred_at=occurred_at,
        outcome=outcome,
        reason_codes=tuple(dict.fromkeys(str(r) for r in reason_codes)),
        action=action,
        correlation_id=correlation_id,
        actor=actor,
        provider=provider,
        connector_version=connector_version,
        payload_fingerprint=payload_fingerprint(payload),
        metadata=_safe(metadata or {}),
        previous_hash=previous,
    )
    return sink.append(evidence)


__all__ = [
    "EvidenceError", "IntegrationEvidence", "EvidenceSink", "MemoryEvidenceStore",
    "payload_fingerprint", "compute_evidence_hash", "emit",
]
