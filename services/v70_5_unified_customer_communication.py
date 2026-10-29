"""V70.5 — Unified Customer Communication Operating Loop.

Communication is a business capability, not a transport feature. This module
normalizes communication intent, channel selection, consent/suppression, and
message identity before work reaches the existing governed automation runtime.

It deliberately performs NO provider side effects. Actual sends remain behind
BusinessAutomation -> Action Kernel -> durable execution/outbox.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


class CommunicationDisposition(str, Enum):
    READY_FOR_GOVERNANCE = "ready_for_governance"
    SUPPRESSED = "suppressed"
    INVALID = "invalid"
    ADVISORY_ONLY = "advisory_only"


class Channel(str, Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"


@dataclass(frozen=True)
class CustomerCommunicationRequest:
    workspace_id: str
    customer_id: str
    event_id: str
    correlation_id: str
    intent: str
    message: str
    channel: str
    recipient: str
    social_account_id: str = ""
    subject: str = ""
    opt_out: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for name in ("workspace_id", "customer_id", "event_id", "correlation_id", "intent", "message", "channel", "recipient"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.channel not in {c.value for c in Channel}:
            raise ValueError("unsupported communication channel")
        if len(self.message) > 10000:
            raise ValueError("message exceeds maximum length")
        if self.channel == Channel.WHATSAPP.value and not self.social_account_id.strip():
            raise ValueError("WhatsApp requires social_account_id")

    @property
    def communication_id(self) -> str:
        canonical = json.dumps({
            "workspace_id": self.workspace_id,
            "customer_id": self.customer_id,
            "event_id": self.event_id,
            "intent": self.intent,
            "channel": self.channel,
        }, sort_keys=True, separators=(",", ":"))
        return "kdo:comm:v70.5:" + hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CommunicationAdmission:
    disposition: CommunicationDisposition
    communication_id: str
    reasons: tuple[str, ...] = ()
    governed_action: str | None = None

    @property
    def sendable(self) -> bool:
        return self.disposition == CommunicationDisposition.READY_FOR_GOVERNANCE


class UnifiedCustomerCommunication:
    """Admission/read model for governed customer communication."""

    VERSION = "70.5.0"
    ACTION = "customer_communication"

    def __init__(self, *, suppressed_customer_ids: Sequence[str] = ()) -> None:
        self._suppressed = {str(x).strip() for x in suppressed_customer_ids if str(x).strip()}

    def suppress_customer(self, customer_id: str) -> None:
        customer_id = str(customer_id or "").strip()
        if customer_id:
            self._suppressed.add(customer_id)

    def is_suppressed(self, customer_id: str) -> bool:
        return str(customer_id or "").strip() in self._suppressed

    def admit(self, request: CustomerCommunicationRequest) -> CommunicationAdmission:
        try:
            request.validate()
        except ValueError as exc:
            return CommunicationAdmission(CommunicationDisposition.INVALID, request.communication_id, (str(exc),))

        if request.opt_out or self.is_suppressed(request.customer_id):
            return CommunicationAdmission(
                CommunicationDisposition.SUPPRESSED,
                request.communication_id,
                ("customer_opted_out_or_suppressed",),
            )

        return CommunicationAdmission(
            CommunicationDisposition.READY_FOR_GOVERNANCE,
            request.communication_id,
            ("communication_requires_governed_action_kernel_execution",),
            governed_action=self.ACTION,
        )

    def build_event(self, request: CustomerCommunicationRequest) -> dict[str, Any]:
        admission = self.admit(request)
        return {
            "event_id": request.event_id,
            "correlation_id": request.correlation_id,
            "workspace_id": request.workspace_id,
            "customer_id": request.customer_id,
            "communication_id": request.communication_id,
            "intent": request.intent,
            "channel": request.channel,
            "recipient": request.recipient,
            "social_account_id": request.social_account_id,
            "subject": request.subject,
            "message": request.message,
            "communication_disposition": admission.disposition.value,
            "communication_reasons": list(admission.reasons),
            "governed_action": admission.governed_action,
        }


__all__ = [
    "Channel",
    "CommunicationAdmission",
    "CommunicationDisposition",
    "CustomerCommunicationRequest",
    "UnifiedCustomerCommunication",
]
