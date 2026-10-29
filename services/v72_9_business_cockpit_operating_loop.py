"""V72.9 — Business Cockpit Operating Loop & Human Decision Workspace.

Composes durable truth, recommendations, decisions, learning and execution
posture into one business operating view. It is deliberately read-only:
command descriptors are intents for the KCPG boundary, never authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json
from typing import Any, Mapping, Sequence


def _fp(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


class LoopState:
    READY = "READY"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    GOVERNANCE_REQUIRED = "GOVERNANCE_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DecisionWorkItem:
    item_id: str
    workspace_id: str
    kind: str
    title: str
    state: str
    priority: str
    recommendation_id: str | None
    decision_id: str | None
    evidence: tuple[Mapping[str, Any], ...]
    command_intent: Mapping[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id, "workspaceId": self.workspace_id,
            "kind": self.kind, "title": self.title, "state": self.state,
            "priority": self.priority, "recommendationId": self.recommendation_id,
            "decisionId": self.decision_id, "evidence": [dict(x) for x in self.evidence],
            "commandIntent": dict(self.command_intent) if self.command_intent else None,
            "actionAuthorityGranted": False,
        }


class BusinessCockpitOperatingLoop:
    VERSION = "v72.9"

    def __init__(self, workspace_id: str, *, truth: Mapping[str, Any] | None = None,
                 recommendations: Sequence[Mapping[str, Any]] = (),
                 decisions: Sequence[Mapping[str, Any]] = (),
                 learning: Mapping[str, Any] | None = None):
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        self.truth = dict(truth or {})
        self.recommendations = [dict(x) for x in recommendations]
        self.decisions = [dict(x) for x in decisions]
        self.learning = dict(learning or {})

    @staticmethod
    def _val(row: Mapping[str, Any], *keys: str) -> Any:
        for k in keys:
            if row.get(k) is not None:
                return row[k]
        return None

    def _decision_for(self, rid: str) -> Mapping[str, Any] | None:
        for d in self.decisions:
            if str(self._val(d, "recommendationId", "recommendation_id")) == rid:
                return d
        return None

    def _item(self, rec: Mapping[str, Any]) -> DecisionWorkItem:
        rid = str(self._val(rec, "recommendationId", "recommendation_id") or "") or None
        d = self._decision_for(rid or "")
        did = str(self._val(d or {}, "decisionId", "decision_id", "id") or "") or None
        cap = str(self._val(rec, "capabilityResolution", "capability_resolution") or "")
        risk = str(self._val(rec, "risk") or "LOW").upper()
        lifecycle = str(self._val(rec, "lifecycle") or "ACTIVE").upper()
        if cap == "GOVERNANCE_REVIEW_REQUIRED" or risk in {"HIGH", "CRITICAL"}:
            state = LoopState.GOVERNANCE_REQUIRED
        elif lifecycle in {"DISMISSED", "SUPERSEDED"}:
            state = LoopState.READY
        else:
            state = LoopState.ATTENTION_REQUIRED
        priority = "CRITICAL" if risk == "CRITICAL" else ("HIGH" if risk == "HIGH" else "NORMAL")
        evidence = list(self._val(rec, "evidence") or [])[:10]
        intent = None
        if cap not in {"ADVISORY_ONLY", "CAPABILITY_UNAVAILABLE", "GOVERNANCE_REVIEW_REQUIRED"} and risk not in {"HIGH", "CRITICAL"}:
            intent = {
                "type": "GOVERNED_ACTION_INTENT",
                "recommendationId": rid,
                "capability": self._val(rec, "capability"),
                "requiresKcpG": True,
                "executionRequested": False,
                "authorityGranted": False,
            }
        canonical = {"workspaceId": self.workspace_id, "recommendationId": rid,
                     "decisionId": did, "state": state, "title": self._val(rec, "title")}
        return DecisionWorkItem(
            item_id="kdo:cockpit-work-item:v72.9:" + _fp(canonical)[:32],
            workspace_id=self.workspace_id,
            kind="DECISION" if d else "RECOMMENDATION",
            title=str(self._val(rec, "title") or "Business decision requires attention"),
            state=state, priority=priority, recommendation_id=rid, decision_id=did,
            evidence=tuple(evidence), command_intent=intent)

    def build(self) -> dict[str, Any]:
        items = [self._item(r) for r in self.recommendations if self._val(r, "recommendationId", "recommendation_id")]
        items.sort(key=lambda x: ({"CRITICAL": 0, "HIGH": 1, "NORMAL": 2}.get(x.priority, 3), x.item_id))
        learning_signals = list(self.learning.get("signals") or [])
        return {
            "schema": "BusinessCockpitOperatingLoop/v1", "version": self.VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(), "workspaceId": self.workspace_id,
            "state": LoopState.ATTENTION_REQUIRED if items else (LoopState.READY if self.truth else LoopState.UNKNOWN),
            "workItems": [x.as_dict() for x in items],
            "summary": {
                "workItems": len(items),
                "governanceRequired": sum(x.state == LoopState.GOVERNANCE_REQUIRED for x in items),
                "attentionRequired": sum(x.state == LoopState.ATTENTION_REQUIRED for x in items),
                "learningSignals": len(learning_signals),
            },
            "operatingLoop": ["TRUTH", "INTELLIGENCE", "RECOMMENDATION", "DECISION", "GOVERNED_EXECUTION", "RESULT", "OUTCOME", "LEARNING"],
            "authority": {
                "readOnly": True, "actionAuthorityGranted": False,
                "workItemsAreCommands": False, "commandIntentsAreAuthority": False,
                "humanDecisionResumesThroughGovernance": True,
                "consequentialCommandsMustUse": ["KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION"],
            },
        }
