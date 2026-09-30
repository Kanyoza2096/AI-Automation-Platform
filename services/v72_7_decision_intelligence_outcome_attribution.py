"""V72.7 — Business Decision Intelligence & Outcome Attribution.

Links durable recommendations to recorded governance decisions, execution/result
identities and measured business outcomes. Attribution is evidence classification,
not causal proof and never grants execution authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json
from typing import Any, Mapping, Sequence


class AttributionState:
    OBSERVED = "OBSERVED"          # direct durable link exists
    ASSOCIATED = "ASSOCIATED"      # evidence is related, but causal linkage is not proven
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _id(prefix: str, value: Any) -> str:
    return f"kdo:{prefix}:v72.7:" + _fp(value)[:32]


@dataclass(frozen=True)
class DecisionLink:
    decision_id: str
    decision_state: str
    decision_source: str
    recommendation_id: str | None
    evidence: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class OutcomeAttribution:
    recommendation_id: str
    outcome_id: str
    state: str
    confidence: str
    evidence: tuple[Mapping[str, Any], ...]
    attribution_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendationId": self.recommendation_id,
            "outcomeId": self.outcome_id,
            "state": self.state,
            "confidence": self.confidence,
            "evidence": [dict(x) for x in self.evidence],
            "attributionNote": self.attribution_note,
            "causalClaim": False,
            "actionAuthorityGranted": False,
        }


class BusinessDecisionIntelligence:
    VERSION = "v72.7"

    def __init__(self, workspace_id: str, recommendations: Sequence[Mapping[str, Any]],
                 decisions: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]]):
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        self.recommendations = [dict(x) for x in recommendations]
        self.decisions = [dict(x) for x in decisions]
        self.outcomes = [dict(x) for x in outcomes]

    @staticmethod
    def _value(row: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if row.get(key) is not None:
                return row.get(key)
        return None

    def _decision_links(self, rec: Mapping[str, Any]) -> list[DecisionLink]:
        rid = str(self._value(rec, "recommendationId", "recommendation_id") or "")
        links: list[DecisionLink] = []
        explicit = self._value(rec, "decisionLinks", "decision_links") or []
        explicit_ids = {str(x) for x in explicit}
        for d in self.decisions:
            did = str(self._value(d, "decisionId", "decision_id", "id") or "")
            linked_rec = str(self._value(d, "recommendationId", "recommendation_id") or "")
            if not did or not (did in explicit_ids or linked_rec == rid):
                continue
            evidence = (
                {"source": "decision", "decisionId": did, "fingerprint": _fp(d)},
                {"source": "recommendation", "recommendationId": rid, "fingerprint": _fp(rec)},
            )
            links.append(DecisionLink(
                decision_id=did,
                decision_state=str(self._value(d, "state", "status") or "UNKNOWN"),
                decision_source=str(self._value(d, "source", "decisionSource") or "governance"),
                recommendation_id=rid,
                evidence=evidence,
            ))
        return links

    def _attributions(self, rec: Mapping[str, Any], decisions: Sequence[DecisionLink]) -> list[OutcomeAttribution]:
        rid = str(self._value(rec, "recommendationId", "recommendation_id") or "")
        explicit = self._value(rec, "outcomeLinks", "outcome_links") or []
        explicit_ids = {str(x) for x in explicit}
        result: list[OutcomeAttribution] = []
        for outcome in self.outcomes:
            oid = str(self._value(outcome, "observationId", "observation_id", "outcomeId", "outcome_id", "id") or "")
            if not oid:
                continue
            linked_rec = str(self._value(outcome, "recommendationId", "recommendation_id") or "")
            linked_dec = str(self._value(outcome, "decisionId", "decision_id") or "")
            direct = oid in explicit_ids or linked_rec == rid or linked_dec in {d.decision_id for d in decisions}
            if not direct:
                continue
            evidence = [{"source": "outcome", "outcomeId": oid, "fingerprint": _fp(outcome)}]
            evidence.extend({"source": "decision", "decisionId": d.decision_id, "fingerprint": _fp(d)} for d in decisions[:3])
            evidence.append({"source": "recommendation", "recommendationId": rid, "fingerprint": _fp(rec)})
            if linked_rec == rid or oid in explicit_ids:
                state, confidence = AttributionState.OBSERVED, "HIGH"
                note = "A durable recommendation-to-outcome link is recorded; this is observed linkage, not causal proof."
            elif linked_dec:
                state, confidence = AttributionState.ASSOCIATED, "MEDIUM"
                note = "The outcome is linked through a recorded decision; causal contribution of the recommendation is not proven."
            else:
                state, confidence = AttributionState.INSUFFICIENT_EVIDENCE, "LOW"
                note = "Related evidence exists, but the durable chain is insufficient to attribute the outcome."
            result.append(OutcomeAttribution(rid, oid, state, confidence, tuple(evidence), note))
        if not result:
            result.append(OutcomeAttribution(
                rid, "", AttributionState.INSUFFICIENT_EVIDENCE, "LOW",
                ({"source": "recommendation", "recommendationId": rid, "fingerprint": _fp(rec)},),
                "No durable outcome link is available yet; effectiveness cannot be inferred."))
        return result

    def build(self) -> dict[str, Any]:
        rows = []
        for rec in self.recommendations:
            rid = str(self._value(rec, "recommendationId", "recommendation_id") or "")
            if not rid:
                continue
            decisions = self._decision_links(rec)
            attrs = self._attributions(rec, decisions)
            decision_data = [{
                "decisionId": d.decision_id,
                "state": d.decision_state,
                "source": d.decision_source,
                "evidence": [dict(x) for x in d.evidence],
                "actionAuthorityGranted": False,
            } for d in decisions]
            rows.append({
                "recommendationId": rid,
                "recommendationFingerprint": self._value(rec, "fingerprint"),
                "lifecycle": self._value(rec, "lifecycle") or "ACTIVE",
                "decisions": decision_data,
                "outcomeAttributions": [a.as_dict() for a in attrs],
                "effectiveness": {
                    "state": "OBSERVED" if any(a.state == AttributionState.OBSERVED for a in attrs) else AttributionState.INSUFFICIENT_EVIDENCE,
                    "causalClaim": False,
                    "authorityGranted": False,
                },
            })
        return {
            "schema": "BusinessDecisionIntelligenceOutcomeAttribution/v1",
            "version": self.VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "workspaceId": self.workspace_id,
            "recommendations": sorted(rows, key=lambda x: x["recommendationId"]),
            "summary": {
                "recommendations": len(rows),
                "withDecisions": sum(bool(x["decisions"]) for x in rows),
                "withObservedOutcomeLink": sum(any(a["state"] == AttributionState.OBSERVED for a in x["outcomeAttributions"]) for x in rows),
                "insufficientEvidence": sum(x["effectiveness"]["state"] == AttributionState.INSUFFICIENT_EVIDENCE for x in rows),
            },
            "authority": {
                "readOnly": True,
                "recommendationsAreDecisions": False,
                "causalClaimsGranted": False,
                "actionAuthorityGranted": False,
                "learningMayRewriteAuthority": False,
                "consequentialCommandsMustUse": ["KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION"],
            },
        }
