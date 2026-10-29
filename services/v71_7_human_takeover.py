"""V71.7 — Governed AI Workforce: Human Takeover.

Creates an explicit, tenant-scoped handoff boundary when human judgment is
required. A takeover pauses the agent workflow; humans may resolve the case,
but this module never executes actions, grants permissions, or bypasses KDO
governance. A resolution is an input to the authoritative governance plane.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus


class TakeoverStatus(str, Enum):
    REQUESTED = "requested"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TakeoverDisposition(str, Enum):
    RESUME_TO_GOVERNANCE = "resume_to_governance"
    REJECT = "reject"
    ESCALATE = "escalate"


class TakeoverBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    DUPLICATE_CASE = "duplicate_takeover_case"
    CASE_NOT_FOUND = "takeover_case_not_found"
    CASE_TAMPERED = "takeover_case_tampered"
    TERMINAL = "takeover_case_terminal"
    INVALID_TRANSITION = "invalid_takeover_transition"
    INVALID_HUMAN = "invalid_human_identity"
    INVALID_DISPOSITION = "invalid_disposition"
    EMPTY_REASON = "empty_takeover_reason"


TERMINAL = frozenset({TakeoverStatus.RESOLVED, TakeoverStatus.EXPIRED, TakeoverStatus.CANCELLED})


@dataclass(frozen=True)
class HumanTakeoverCase:
    case_id: str
    workspace_id: str
    agent_id: str
    reason: str
    trigger: str
    context: Mapping[str, Any] = field(default_factory=dict)
    status: TakeoverStatus = TakeoverStatus.REQUESTED
    claimed_by: Optional[str] = None
    disposition: Optional[TakeoverDisposition] = None
    human_note: Optional[str] = None
    version: str = "1"
    case_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "workspace_id": self.workspace_id,
            "agent_id": self.agent_id, "reason": self.reason,
            "trigger": self.trigger, "context": dict(self.context),
            "status": self.status.value, "claimed_by": self.claimed_by,
            "disposition": self.disposition.value if self.disposition else None,
            "human_note": self.human_note, "version": self.version,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class HumanTakeoverRegistry:
    VERSION = "v71.7"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws:
            raise ValueError(TakeoverBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self._cases: dict[str, HumanTakeoverCase] = {}

    def _active_agent(self, agent_id: str):
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(TakeoverBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if agent.status != AgentStatus.ACTIVE:
            raise ValueError(TakeoverBlock.AGENT_NOT_ACTIVE.value)
        return agent

    def create(self, *, case_id: str, agent_id: str, reason: str, trigger: str,
               context: Optional[Mapping[str, Any]] = None, version: str = "1") -> HumanTakeoverCase:
        cid, why, trig = str(case_id or "").strip(), str(reason or "").strip(), str(trigger or "").strip()
        if not cid or cid in self._cases:
            raise ValueError(TakeoverBlock.DUPLICATE_CASE.value)
        if not why or not trig:
            raise ValueError(TakeoverBlock.EMPTY_REASON.value)
        if not str(version or "").strip():
            raise ValueError(TakeoverBlock.INVALID_TRANSITION.value)
        self._active_agent(agent_id)
        case = HumanTakeoverCase(cid, self.workspace_id, str(agent_id), why, trig, dict(context or {}), version=str(version))
        case = HumanTakeoverCase(**{**case.__dict__, "case_fingerprint": case.fingerprint()})
        self._cases[cid] = case
        return case

    def get(self, case_id: str) -> Optional[HumanTakeoverCase]:
        return self._cases.get(str(case_id))

    def assert_integrity(self, case: HumanTakeoverCase) -> None:
        if case.workspace_id != self.workspace_id:
            raise ValueError(TakeoverBlock.TENANT_MISMATCH.value)
        if case.case_fingerprint != case.fingerprint():
            raise ValueError(TakeoverBlock.CASE_TAMPERED.value)

    def claim(self, case_id: str, *, human_id: str) -> HumanTakeoverCase:
        case = self.get(case_id)
        if case is None: raise ValueError(TakeoverBlock.CASE_NOT_FOUND.value)
        self.assert_integrity(case)
        human = str(human_id or "").strip()
        if not human: raise ValueError(TakeoverBlock.INVALID_HUMAN.value)
        if case.status != TakeoverStatus.REQUESTED:
            raise ValueError(TakeoverBlock.INVALID_TRANSITION.value)
        updated = HumanTakeoverCase(**{**case.__dict__, "status": TakeoverStatus.CLAIMED, "claimed_by": human, "case_fingerprint": ""})
        updated = HumanTakeoverCase(**{**updated.__dict__, "case_fingerprint": updated.fingerprint()})
        self._cases[case_id] = updated
        return updated

    def resolve(self, case_id: str, *, human_id: str, disposition: TakeoverDisposition,
                note: Optional[str] = None) -> HumanTakeoverCase:
        case = self.get(case_id)
        if case is None: raise ValueError(TakeoverBlock.CASE_NOT_FOUND.value)
        self.assert_integrity(case)
        human = str(human_id or "").strip()
        if not human or human != case.claimed_by:
            raise ValueError(TakeoverBlock.INVALID_HUMAN.value)
        if case.status != TakeoverStatus.CLAIMED:
            raise ValueError(TakeoverBlock.INVALID_TRANSITION.value)
        try: disp = TakeoverDisposition(disposition)
        except ValueError: raise ValueError(TakeoverBlock.INVALID_DISPOSITION.value)
        updated = HumanTakeoverCase(**{**case.__dict__, "status": TakeoverStatus.RESOLVED,
            "disposition": disp, "human_note": None if note is None else str(note), "case_fingerprint": ""})
        updated = HumanTakeoverCase(**{**updated.__dict__, "case_fingerprint": updated.fingerprint()})
        self._cases[case_id] = updated
        return updated

    def cancel(self, case_id: str) -> HumanTakeoverCase:
        case = self.get(case_id)
        if case is None: raise ValueError(TakeoverBlock.CASE_NOT_FOUND.value)
        self.assert_integrity(case)
        if case.status in TERMINAL: raise ValueError(TakeoverBlock.TERMINAL.value)
        updated = HumanTakeoverCase(**{**case.__dict__, "status": TakeoverStatus.CANCELLED, "case_fingerprint": ""})
        updated = HumanTakeoverCase(**{**updated.__dict__, "case_fingerprint": updated.fingerprint()})
        self._cases[case_id] = updated
        return updated

    def handoff_context(self, case_id: str) -> Mapping[str, Any]:
        case = self.get(case_id)
        if case is None: raise ValueError(TakeoverBlock.CASE_NOT_FOUND.value)
        self.assert_integrity(case)
        return {"case_id": case.case_id, "agent_id": case.agent_id, "status": case.status.value,
                "reason": case.reason, "trigger": case.trigger, "context": dict(case.context),
                "disposition": case.disposition.value if case.disposition else None,
                "advisory_only": True, "authority_granted": False}

    def pending(self, *, agent_id: Optional[str] = None) -> tuple[HumanTakeoverCase, ...]:
        if agent_id is not None: self._active_agent(agent_id)
        return tuple(c for c in self._cases.values()
                     if c.status in {TakeoverStatus.REQUESTED, TakeoverStatus.CLAIMED}
                     and (agent_id is None or c.agent_id == str(agent_id)))


__all__ = ["TakeoverStatus", "TakeoverDisposition", "TakeoverBlock", "HumanTakeoverCase", "HumanTakeoverRegistry"]
