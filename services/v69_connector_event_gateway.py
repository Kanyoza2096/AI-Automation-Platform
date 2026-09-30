"""V69.11 — connector event runtime ingress gateway.

The outbound runtime gateway protects provider mutations. This module protects
external events entering KDO. It authenticates the source, binds the event to
one tenant/connection, verifies the declared inbound event and mapping, fences
replays, and produces only a canonical event envelope. It never invokes
BusinessAutomation, the Decision Plane, or an Action Kernel directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Protocol
from uuid import uuid4

from services.v69_integration_evidence import EvidenceSink, emit

from services.business_integration_platform import ConnectionState, ConnectorContract, IntegrationConnection
from services.v69_integration_foundation import EventMapping, validate_event_mapping


class IngressBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    CONNECTION_MISMATCH = "connection_mismatch"
    CONNECTION_NOT_READY = "connection_not_ready"
    CONNECTION_REVOKED = "connection_revoked"
    EVENT_NOT_DECLARED = "event_not_declared"
    MAPPING_INVALID = "mapping_invalid"
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_SIGNATURE = "invalid_signature"
    STALE_EVENT = "stale_event"
    REPLAY = "replay"
    MISSING_EVENT_ID = "missing_event_id"
    PAYLOAD_INVALID = "payload_invalid"
    TENANT_CLAIM_MISSING = "tenant_claim_missing"
    TENANT_CLAIM_MISMATCH = "tenant_claim_mismatch"


class IngressRejected(PermissionError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("connector event ingress rejected: " + ",".join(reasons))


class ReplayFence(Protocol):
    def seen(self, workspace_id: str, connection_id: str, event_id: str) -> bool: ...
    def reserve(self, workspace_id: str, connection_id: str, event_id: str) -> bool: ...


class MemoryReplayFence:
    """Small deterministic fence for tests/process-local adapters.

    Production adapters should implement the same interface with a durable
    tenant-scoped unique constraint, such as the existing event receipt RPC.
    """
    def __init__(self) -> None:
        self._ids: set[tuple[str, str, str]] = set()

    def seen(self, workspace_id: str, connection_id: str, event_id: str) -> bool:
        return (workspace_id, connection_id, event_id) in self._ids

    def reserve(self, workspace_id: str, connection_id: str, event_id: str) -> bool:
        key = (workspace_id, connection_id, event_id)
        if key in self._ids:
            return False
        self._ids.add(key)
        return True


@dataclass(frozen=True)
class InboundAuthentication:
    authenticated: bool
    tenant_verified: bool
    signature_verified: bool
    external_subject: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class CanonicalInboundEvent:
    workspace_id: str
    connection_id: str
    connector_key: str
    external_event: str
    canonical_event: str
    event_id: str
    payload: dict[str, Any]
    source: str
    correlation_id: str | None = None


@dataclass(frozen=True)
class IngressDecision:
    accepted: bool
    event: CanonicalInboundEvent | None = None
    reasons: tuple[str, ...] = ()
    replay: bool = False


def verify_hmac_sha256(secret: str, body: bytes | str, signature: str, *, prefix: str = "sha256=") -> bool:
    if not secret or not signature:
        return False
    raw = body.encode() if isinstance(body, str) else body
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    supplied = signature.strip()
    if supplied.startswith(prefix):
        supplied = supplied[len(prefix):]
    return hmac.compare_digest(expected, supplied)


@dataclass(frozen=True)
class ConnectorEventRuntimeGateway:
    workspace_id: str
    connection: IntegrationConnection
    contract: ConnectorContract
    mapping: EventMapping
    replay_fence: ReplayFence
    max_age_seconds: int = 300
    evidence_sink: EvidenceSink | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError(IngressBlock.TENANT_MISMATCH.value)
        if self.connection.workspace_id != self.workspace_id or self.mapping.workspace_id != self.workspace_id:
            raise ValueError(IngressBlock.TENANT_MISMATCH.value)
        if self.mapping.connection_id != self.connection.connection_id:
            raise ValueError(IngressBlock.CONNECTION_MISMATCH.value)
        if self.connection.connector_key != self.contract.connector_key:
            raise ValueError(IngressBlock.CONNECTION_MISMATCH.value)
        validate_event_mapping(self.mapping, self.connection, self.contract)

    def authenticate_hmac(
        self,
        *,
        body: bytes | str,
        signature: str,
        tenant_claim: str | None = None,
        secret: str,
        external_subject: str = "",
    ) -> InboundAuthentication:
        if not tenant_claim:
            return InboundAuthentication(False, False, False, external_subject, IngressBlock.TENANT_CLAIM_MISSING.value)
        if tenant_claim != self.workspace_id:
            return InboundAuthentication(False, False, False, external_subject, IngressBlock.TENANT_CLAIM_MISMATCH.value)
        verified = verify_hmac_sha256(secret, body, signature)
        return InboundAuthentication(verified, verified, verified, external_subject, "hmac_sha256" if verified else IngressBlock.INVALID_SIGNATURE.value)

    def ingest(
        self,
        *,
        external_event: str,
        payload: Mapping[str, Any],
        authentication: InboundAuthentication,
        event_id: str | None = None,
        received_at: float | None = None,
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> IngressDecision:
        reasons: list[str] = []
        if self.connection.workspace_id != self.workspace_id or self.mapping.workspace_id != self.workspace_id:
            reasons.append(IngressBlock.TENANT_MISMATCH.value)
        if self.connection.connection_id != self.mapping.connection_id:
            reasons.append(IngressBlock.CONNECTION_MISMATCH.value)
        if self.connection.state in {ConnectionState.REVOKED, ConnectionState.SUSPENDED}:
            reasons.append(IngressBlock.CONNECTION_REVOKED.value)
        elif self.connection.state not in {
            ConnectionState.MAPPING_REQUIRED, ConnectionState.CERTIFICATION_REQUIRED,
            ConnectionState.OBSERVE_ONLY, ConnectionState.SUGGEST_ENABLED,
            ConnectionState.EXECUTION_ENABLED,
        }:
            reasons.append(IngressBlock.CONNECTION_NOT_READY.value)
        if external_event != self.mapping.external_event or external_event not in self.contract.inbound_events:
            reasons.append(IngressBlock.EVENT_NOT_DECLARED.value)
        if not authentication.authenticated or not authentication.signature_verified or not authentication.tenant_verified:
            reasons.append(IngressBlock.AUTHENTICATION_REQUIRED.value)
        if not isinstance(payload, Mapping):
            reasons.append(IngressBlock.PAYLOAD_INVALID.value)
            payload = {}

        canonical: dict[str, Any] = {}
        resolved_id = str(event_id or "").strip()
        if not resolved_id:
            try:
                canonical = self.mapping.canonical_payload(payload)
                resolved_id = str(canonical.get("event_id") or "").strip()
            except Exception:
                reasons.append(IngressBlock.MAPPING_INVALID.value)
        else:
            try:
                mapped = self.mapping.canonical_payload(payload)
                canonical.update(mapped)
            except Exception:
                reasons.append(IngressBlock.MAPPING_INVALID.value)
        if not resolved_id:
            reasons.append(IngressBlock.MISSING_EVENT_ID.value)
        if resolved_id and self.replay_fence.seen(self.workspace_id, self.connection.connection_id, resolved_id):
            if self.evidence_sink is not None:
                emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id=self.connection.connection_id, connector_key=self.contract.connector_key, event_type="ingress_reject", outcome="blocked", reason_codes=(IngressBlock.REPLAY.value,), action=external_event)
            if self.evidence_sink is not None:
                emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id=self.connection.connection_id, connector_key=self.contract.connector_key, event_type="ingress_reject", outcome="blocked", reason_codes=(IngressBlock.REPLAY.value,), action=external_event)
            return IngressDecision(False, reasons=(IngressBlock.REPLAY.value,), replay=True)

        now = time.time()
        if received_at is not None and (received_at > now + 30 or now - received_at > self.max_age_seconds):
            reasons.append(IngressBlock.STALE_EVENT.value)

        if reasons:
            reasons_tuple = tuple(dict.fromkeys(reasons))
            if self.evidence_sink is not None:
                emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id=self.connection.connection_id, connector_key=self.contract.connector_key, event_type="ingress_reject", outcome="blocked", reason_codes=reasons_tuple, action=external_event, payload=payload)
            return IngressDecision(False, reasons=reasons_tuple)
        if not self.replay_fence.reserve(self.workspace_id, self.connection.connection_id, resolved_id):
            return IngressDecision(False, reasons=(IngressBlock.REPLAY.value,), replay=True)

        canonical["event_id"] = resolved_id
        canonical["workspace_id"] = self.workspace_id
        canonical["connection_id"] = self.connection.connection_id
        canonical["external_event"] = external_event
        canonical["external_subject"] = authentication.external_subject
        event = CanonicalInboundEvent(
            workspace_id=self.workspace_id,
            connection_id=self.connection.connection_id,
            connector_key=self.contract.connector_key,
            external_event=external_event,
            canonical_event=self.mapping.canonical_event,
            event_id=resolved_id,
            payload=canonical,
            source=source or f"connector:{self.contract.connector_key}:{self.connection.connection_id}",
            correlation_id=correlation_id,
        )
        if self.evidence_sink is not None:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id, connection_id=self.connection.connection_id, connector_key=self.contract.connector_key, event_type="ingress_accept", outcome="accepted", action=external_event, correlation_id=correlation_id, payload=payload)
        return IngressDecision(True, event=event)


__all__ = [
    "IngressBlock", "IngressRejected", "ReplayFence", "MemoryReplayFence",
    "InboundAuthentication", "CanonicalInboundEvent", "IngressDecision",
    "verify_hmac_sha256", "ConnectorEventRuntimeGateway",
]
