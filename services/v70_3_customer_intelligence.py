"""V70.3 — Customer Intelligence & Relationship Context.

Builds a tenant-bound relationship context from CRM/customer observations. This is
an intelligence/read model: it may recommend the next governed workflow, but it
cannot authorize or execute it.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json


class IntelligenceBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    MISSING_CUSTOMER_ID = "missing_customer_id"
    MISSING_EVENT_ID = "missing_event_id"
    DUPLICATE_EVENT = "duplicate_event"
    INVALID_SIGNAL = "invalid_signal"
    OPTED_OUT = "opted_out"


@dataclass(frozen=True)
class CustomerIdentity:
    workspace_id: str
    customer_id: str
    name: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""


@dataclass(frozen=True)
class CustomerSignal:
    event_id: str
    signal_type: str
    value: float = 1.0
    source: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationshipContext:
    workspace_id: str
    customer_id: str
    identity: CustomerIdentity
    lifecycle_state: str = "unknown"
    interaction_count: int = 0
    positive_signals: float = 0.0
    negative_signals: float = 0.0
    last_signal_type: str = ""
    last_signal_source: str = ""
    recommended_workflow: Optional[str] = None
    recommendation_reasons: tuple[str, ...] = ()
    advisory_only: bool = True
    authority: str = "none"
    execution_effect: str = "none"

    def __post_init__(self) -> None:
        if self.advisory_only is not True or self.authority != "none" or self.execution_effect != "none":
            raise ValueError("customer intelligence cannot grant authority")

    def fingerprint(self) -> str:
        payload = {"workspace_id": self.workspace_id, "customer_id": self.customer_id,
                   "identity": self.identity.__dict__, "lifecycle_state": self.lifecycle_state,
                   "interaction_count": self.interaction_count,
                   "positive_signals": self.positive_signals,
                   "negative_signals": self.negative_signals,
                   "last_signal_type": self.last_signal_type,
                   "recommended_workflow": self.recommended_workflow,
                   "recommendation_reasons": self.recommendation_reasons}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class CustomerIntelligence:
    VERSION = "v70.3"
    POSITIVE = frozenset({"reply", "engaged", "qualified", "purchase_intent", "meeting_booked", "positive_feedback"})
    NEGATIVE = frozenset({"negative_feedback", "unresponsive", "lost", "complaint"})

    def __init__(self, workspace_id: str):
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        self.workspace_id = str(workspace_id)
        self._identities: dict[str, CustomerIdentity] = {}
        self._states: dict[str, str] = {}
        self._signals: dict[str, list[CustomerSignal]] = {}
        self._events: set[str] = set()
        self._opted_out: set[str] = set()

    def observe_identity(self, identity: CustomerIdentity) -> CustomerIdentity:
        self._tenant(identity.workspace_id)
        if not identity.customer_id.strip():
            raise ValueError(IntelligenceBlock.MISSING_CUSTOMER_ID.value)
        current = self._identities.get(identity.customer_id)
        merged = CustomerIdentity(self.workspace_id, identity.customer_id,
            identity.name or (current.name if current else ""), identity.email or (current.email if current else ""),
            identity.phone or (current.phone if current else ""), identity.company or (current.company if current else ""))
        self._identities[identity.customer_id] = merged
        return merged

    def observe(self, *, event_id: str, customer_id: str, signal_type: str,
                source: str = "", value: float = 1.0, metadata: Optional[Mapping[str, Any]] = None,
                workspace_id: Optional[str] = None, lifecycle_state: Optional[str] = None) -> RelationshipContext:
        if not str(event_id).strip(): raise ValueError(IntelligenceBlock.MISSING_EVENT_ID.value)
        if event_id in self._events: raise ValueError(IntelligenceBlock.DUPLICATE_EVENT.value)
        self._tenant(workspace_id or self.workspace_id)
        if not str(customer_id).strip(): raise ValueError(IntelligenceBlock.MISSING_CUSTOMER_ID.value)
        if not str(signal_type).strip() or not isinstance(value, (int, float)):
            raise ValueError(IntelligenceBlock.INVALID_SIGNAL.value)
        if customer_id in self._opted_out and signal_type != "opt_out":
            raise ValueError(IntelligenceBlock.OPTED_OUT.value)
        sig = CustomerSignal(str(event_id), str(signal_type), float(value), str(source), dict(metadata or {}))
        self._signals.setdefault(customer_id, []).append(sig)
        self._events.add(str(event_id))
        if lifecycle_state: self._states[customer_id] = str(lifecycle_state)
        if signal_type == "opt_out": self._opted_out.add(customer_id); self._states[customer_id] = "opted_out"
        return self.context(customer_id)

    def recommend_next_workflow(self, customer_id: str) -> RelationshipContext:
        ctx = self.context(customer_id)
        if customer_id in self._opted_out:
            return ctx
        signals = self._signals.get(customer_id, [])
        if ctx.lifecycle_state in {"won", "lost"}:
            return ctx
        if any(s.signal_type in {"purchase_intent", "meeting_booked"} for s in signals):
            return self._replace(ctx, "sales.high-intent-follow-up", ("high_intent_signal",))
        if ctx.lifecycle_state == "follow_up_due" or any(s.signal_type == "unresponsive" for s in signals):
            return self._replace(ctx, "sales.follow-up-due", ("follow_up_due",))
        if ctx.lifecycle_state == "qualified" or any(s.signal_type == "qualified" for s in signals):
            return self._replace(ctx, "sales.qualified-lead-status", ("qualified_customer",))
        if signals:
            return self._replace(ctx, "sales.customer-engagement", ("recent_customer_signal",))
        return ctx

    def context(self, customer_id: str) -> RelationshipContext:
        cid = str(customer_id).strip()
        if not cid: raise ValueError(IntelligenceBlock.MISSING_CUSTOMER_ID.value)
        identity = self._identities.get(cid, CustomerIdentity(self.workspace_id, cid))
        signals = self._signals.get(cid, [])
        pos = sum(max(0.0, s.value) for s in signals if s.signal_type in self.POSITIVE)
        neg = sum(max(0.0, s.value) for s in signals if s.signal_type in self.NEGATIVE)
        return RelationshipContext(self.workspace_id, cid, identity, self._states.get(cid, "unknown"),
            len(signals), pos, neg, signals[-1].signal_type if signals else "", signals[-1].source if signals else "")

    def _replace(self, ctx: RelationshipContext, workflow: str, reasons: tuple[str, ...]) -> RelationshipContext:
        return RelationshipContext(**{**ctx.__dict__, "recommended_workflow": workflow, "recommendation_reasons": reasons})

    def _tenant(self, workspace_id: str) -> None:
        if str(workspace_id) != self.workspace_id: raise ValueError(IntelligenceBlock.TENANT_MISMATCH.value)
