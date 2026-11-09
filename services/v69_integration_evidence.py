"""V69.13 — tenant-scoped integration observability and audit evidence.

Evidence is descriptive, not authoritative. It records what the control plane
observed/decided, never grants authority, stores secrets, or executes providers.
The Supabase sink persists append-only evidence through an atomic database RPC;
the in-memory store remains deterministic for tests and offline certification.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Protocol
from uuid import uuid4


class EvidenceError(ValueError):
    pass


class EvidenceChainConflict(EvidenceError):
    """The durable evidence head changed while an append was being prepared."""


_SECRET_KEYS = {
    "secret", "token", "access_token", "refresh_token", "api_key", "apikey",
    "authorization", "credential", "credentials", "password", "client_secret",
    "private_key", "signature", "raw_secret", "secret_ref_value",
    "page_access_token", "page_token", "oauth_token", "bearer_token",
    "token_value", "api_secret", "signing_key", "private_seed",
    "ed25519_private_key",
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
        if not isinstance(self.metadata, Mapping):
            raise EvidenceError("evidence metadata must be a mapping")
        object.__setattr__(self, "metadata", _safe(self.metadata))
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


@dataclass(frozen=True)
class DurableHealthObservation:
    evidence_id: str
    observation_id: str
    workspace_id: str
    connection_id: str
    connector_key: str
    observed_at: datetime
    health_state: str
    evidence_hash: str


class SupabaseIntegrationEvidenceStore:
    """Durable append-only sink using the atomic append_integration_evidence RPC.

    The client must be an explicitly injected trusted server-side Supabase
    service-role client. This class never creates a client or falls back to
    memory. The SQL RPC serializes appends per workspace/connection and checks
    the expected previous hash before inserting.
    """

    def __init__(self, client: Any, *, max_retries: int = 3) -> None:
        if client is None:
            raise EvidenceError("evidence_database_client_required")
        if not isinstance(max_retries, int) or not 1 <= max_retries <= 10:
            raise EvidenceError("max_retries must be between 1 and 10")
        self._client = client
        self._max_retries = max_retries

    def last_hash(self, workspace_id: str, connection_id: str) -> str:
        if not workspace_id or not connection_id:
            raise EvidenceError("workspace_and_connection_required")
        try:
            response = (
                self._client.table("integration_evidence")
                .select("evidence_hash")
                .eq("workspace_id", workspace_id)
                .eq("connection_id", connection_id)
                .order("sequence_no", desc=True)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                return ""
            return str(rows[0].get("evidence_hash") or "").strip()
        except Exception as exc:
            raise EvidenceError("evidence_head_read_failed") from exc

    def append(self, evidence: IntegrationEvidence) -> IntegrationEvidence:
        current = evidence
        for _attempt in range(self._max_retries):
            expected_previous = self.last_hash(current.workspace_id, current.connection_id)
            if current.previous_hash != expected_previous:
                current = replace(current, previous_hash=expected_previous, evidence_hash="")
            if len(current.payload_fingerprint) != 64:
                raise EvidenceError("payload_fingerprint_required")
            if current.evidence_hash != compute_evidence_hash(current):
                raise EvidenceError("evidence_hash_mismatch")

            params = {
                "p_evidence_id": current.evidence_id,
                "p_workspace_id": current.workspace_id,
                "p_connection_id": current.connection_id,
                "p_connector_key": current.connector_key,
                "p_event_type": current.event_type,
                "p_occurred_at": current.occurred_at.astimezone(timezone.utc).isoformat(),
                "p_outcome": current.outcome,
                "p_reason_codes": list(current.reason_codes),
                "p_action": current.action,
                "p_correlation_id": current.correlation_id,
                "p_actor": current.actor,
                "p_provider": current.provider,
                "p_connector_version": current.connector_version,
                "p_payload_fingerprint": current.payload_fingerprint,
                "p_metadata": _safe(current.metadata),
                "p_expected_previous_hash": current.previous_hash,
                "p_evidence_hash": current.evidence_hash,
            }
            try:
                response = self._client.rpc("append_integration_evidence", params).execute()
            except Exception as exc:
                message = str(exc).lower()
                if "evidence_chain_conflict" in message or "40001" in message:
                    continue
                if "duplicate key" in message or "unique constraint" in message:
                    raise EvidenceError("duplicate_evidence_id") from exc
                raise EvidenceError("evidence_persist_failed") from exc

            data = getattr(response, "data", None)
            confirmed = (
                data == [{"evidence_id": current.evidence_id}]
                or data == {"evidence_id": current.evidence_id}
                or data == current.evidence_id
            )
            if not confirmed:
                raise EvidenceError("evidence_persist_unconfirmed")
            return current

        raise EvidenceChainConflict("evidence_chain_conflict")

    def record_health_observation(
        self,
        *,
        workspace_id: str,
        connection_id: str,
        connector_key: str,
        health_state: str,
        observed_at: datetime | None = None,
        drift: Iterable[str] = (),
        provider_status_code: int | None = None,
        latency_ms: int | None = None,
        reason_codes: Iterable[str] = (),
        actor: str = "system",
        provider: str = "",
        connector_version: str = "",
        metadata: Mapping[str, Any] | None = None,
        evidence_id: str | None = None,
    ) -> DurableHealthObservation:
        """Atomically append a health evidence row and its read-model observation."""
        allowed_states = {"healthy", "degraded", "unavailable", "unknown"}
        if health_state not in allowed_states:
            raise EvidenceError("connector_health_state_invalid")
        if latency_ms is not None and latency_ms < 0:
            raise EvidenceError("connector_health_latency_invalid")
        observed_at = observed_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise EvidenceError("observed_at_must_be_timezone_aware")
        evidence_id = evidence_id or str(uuid4())
        if not evidence_id.strip():
            raise EvidenceError("evidence_id_required")

        drift_values = tuple(dict.fromkeys(str(item) for item in drift))
        reason_values = tuple(dict.fromkeys(str(item) for item in reason_codes))
        payload = {
            "health_state": health_state,
            "drift": list(drift_values),
            "provider_status_code": provider_status_code,
            "latency_ms": latency_ms,
        }
        current = IntegrationEvidence(
            evidence_id=evidence_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
            connector_key=connector_key,
            event_type="connector.health.observed",
            occurred_at=observed_at,
            outcome=health_state,
            reason_codes=reason_values,
            actor=actor,
            provider=provider,
            connector_version=connector_version,
            payload_fingerprint=payload_fingerprint(payload),
            metadata=_safe(metadata or {}),
            previous_hash=self.last_hash(workspace_id, connection_id),
        )

        for _attempt in range(self._max_retries):
            expected_previous = self.last_hash(current.workspace_id, current.connection_id)
            if current.previous_hash != expected_previous:
                current = replace(current, previous_hash=expected_previous, evidence_hash="")
            if current.evidence_hash != compute_evidence_hash(current):
                raise EvidenceError("evidence_hash_mismatch")

            params = {
                "p_evidence_id": current.evidence_id,
                "p_workspace_id": current.workspace_id,
                "p_connection_id": current.connection_id,
                "p_connector_key": current.connector_key,
                "p_observed_at": current.occurred_at.astimezone(timezone.utc).isoformat(),
                "p_health_state": health_state,
                "p_drift": list(drift_values),
                "p_provider_status_code": provider_status_code,
                "p_latency_ms": latency_ms,
                "p_reason_codes": list(current.reason_codes),
                "p_actor": current.actor,
                "p_provider": current.provider,
                "p_connector_version": current.connector_version,
                "p_payload_fingerprint": current.payload_fingerprint,
                "p_metadata": _safe(current.metadata),
                "p_expected_previous_hash": current.previous_hash,
                "p_evidence_hash": current.evidence_hash,
            }
            try:
                response = self._client.rpc("record_connector_health_observation", params).execute()
            except Exception as exc:
                message = str(exc).lower()
                if "evidence_chain_conflict" in message or "40001" in message:
                    continue
                if "duplicate key" in message or "unique constraint" in message:
                    raise EvidenceError("duplicate_evidence_id") from exc
                raise EvidenceError("health_observation_persist_failed") from exc

            data = getattr(response, "data", None)
            row = data[0] if isinstance(data, list) and data else data
            if not isinstance(row, Mapping):
                raise EvidenceError("health_observation_persist_unconfirmed")
            returned_evidence_id = str(row.get("evidence_id") or "")
            observation_id = str(row.get("observation_id") or "")
            if returned_evidence_id != current.evidence_id or not observation_id:
                raise EvidenceError("health_observation_persist_unconfirmed")
            return DurableHealthObservation(
                evidence_id=current.evidence_id,
                observation_id=observation_id,
                workspace_id=current.workspace_id,
                connection_id=current.connection_id,
                connector_key=current.connector_key,
                observed_at=current.occurred_at,
                health_state=health_state,
                evidence_hash=current.evidence_hash,
            )

        raise EvidenceChainConflict("evidence_chain_conflict")


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
    head_reader = getattr(sink, "last_hash", None)
    previous = head_reader(workspace_id, connection_id) if callable(head_reader) else ""
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
    "EvidenceError", "EvidenceChainConflict", "IntegrationEvidence", "EvidenceSink",
    "MemoryEvidenceStore", "SupabaseIntegrationEvidenceStore", "DurableHealthObservation",
    "payload_fingerprint", "compute_evidence_hash", "emit",
]
