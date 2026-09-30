"""V72.5 — Business Intelligence / Decision Surface.

Turns durable Business Cockpit truth into evidence-backed signals and advisory
recommendations. This is intelligence, not authority: nothing here executes,
approves, mutates policy, or calls providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from services.v72_2_command_center_read_model import TruthValue, SECTIONS


class IntelligenceState:
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    ADVISORY = "ADVISORY"
    UNKNOWN = "UNKNOWN"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    GOVERNANCE_REVIEW_REQUIRED = "GOVERNANCE_REVIEW_REQUIRED"


@dataclass(frozen=True)
class IntelligenceRecommendation:
    recommendation_id: str
    workspace_id: str
    objective_id: str | None
    domain: str
    title: str
    rationale: str
    state: str
    confidence: str
    expected_impact: float | None
    capability: str
    capability_resolution: str
    risk: str
    evidence: tuple[Mapping[str, Any], ...]
    outcome_links: tuple[str, ...] = ()
    fingerprint: str = ""

    def canonical(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "workspace_id": self.workspace_id,
            "objective_id": self.objective_id,
            "domain": self.domain,
            "title": self.title,
            "rationale": self.rationale,
            "state": self.state,
            "confidence": self.confidence,
            "expected_impact": self.expected_impact,
            "capability": self.capability,
            "capability_resolution": self.capability_resolution,
            "risk": self.risk,
            "evidence": [dict(x) for x in self.evidence],
            "outcome_links": list(self.outcome_links),
        }

    def with_fingerprint(self) -> "IntelligenceRecommendation":
        fp = hashlib.sha256(json.dumps(self.canonical(), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        rid = "kdo:recommendation:v72.5:" + fp[:32]
        return IntelligenceRecommendation(**{**self.__dict__, "recommendation_id": rid, "fingerprint": fp})

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.canonical(),
            "recommendationId": self.recommendation_id,
            "workspaceId": self.workspace_id,
            "objectiveId": self.objective_id,
            "expectedImpact": self.expected_impact,
            "capabilityResolution": self.capability_resolution,
            "outcomeLinks": list(self.outcome_links),
            "fingerprint": self.fingerprint,
            "actionAuthorityGranted": False,
        }


class BusinessIntelligenceDecisionSurface:
    VERSION = "v72.5"

    def __init__(self, workspace_id: str, truth: Mapping[str, TruthValue] | Mapping[str, Any]):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")
        self.workspace_id = ws
        self.truth = truth

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fp(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

    def _section(self, key: str) -> TruthValue | None:
        value = self.truth.get(key)
        return value if isinstance(value, TruthValue) else None

    def _items(self, key: str) -> list[Mapping[str, Any]]:
        section = self._section(key)
        if section is None or section.state == "UNKNOWN":
            return []
        data = section.data or {}
        return list(data.get("items") or [])

    def _evidence(self, section_key: str, item: Mapping[str, Any], index: int = 0) -> tuple[Mapping[str, Any], ...]:
        section = self._section(section_key)
        refs: list[Mapping[str, Any]] = []
        if section:
            refs.extend(dict(x) for x in section.evidence[:3])
        refs.append({"section": section_key, "itemFingerprint": self._fp(item), "index": index})
        return tuple(refs)

    def _recommendation(self, *, objective_id: str | None, domain: str, title: str,
                        rationale: str, confidence: str, impact: float | None,
                        capability: str, resolution: str, risk: str,
                        evidence: tuple[Mapping[str, Any], ...], state: str = IntelligenceState.ADVISORY,
                        outcome_links: tuple[str, ...] = ()) -> IntelligenceRecommendation:
        rec = IntelligenceRecommendation(
            recommendation_id="pending", workspace_id=self.workspace_id,
            objective_id=objective_id, domain=domain, title=title, rationale=rationale,
            state=state, confidence=confidence, expected_impact=impact,
            capability=capability, capability_resolution=resolution, risk=risk,
            evidence=evidence, outcome_links=outcome_links,
        )
        return rec.with_fingerprint()

    def build(self) -> dict[str, Any]:
        recommendations: list[IntelligenceRecommendation] = []
        objectives = self._items("objectives")
        objective_id = next((str(x.get("objectiveId")) for x in objectives if x.get("state") == "active"), None)

        # Operational pressure: overdue/high-priority work becomes an advisory signal.
        risks = self._items("risks")
        urgent = [x for x in risks if str(x.get("priority", "")).lower() in {"high", "critical"} or x.get("slaBreached") is True]
        for i, item in enumerate(urgent[:5]):
            recommendations.append(self._recommendation(
                objective_id=objective_id, domain="operations",
                title=f"Review operational exception: {item.get('title') or item.get('workId') or 'unidentified work'}",
                rationale="A durable business work record is marked high/critical priority or has breached its SLA; review the evidence before choosing a governed response.",
                confidence="HIGH", impact=None, capability="internal-attention",
                resolution="EXECUTABLE_CANDIDATE", risk="MEDIUM", evidence=self._evidence("risks", item, i)))

        # Pipeline opportunity signal: qualified/proposal/negotiation records.
        opportunities = self._items("opportunities")
        pipeline = [x for x in opportunities if str(x.get("stage", "")).upper() in {"QUALIFIED", "DISCOVERY", "PROPOSAL", "NEGOTIATION"}]
        for i, item in enumerate(pipeline[:5]):
            amount = item.get("amount")
            recommendations.append(self._recommendation(
                objective_id=objective_id, domain="sales",
                title=f"Review sales opportunity: {item.get('opportunityId') or 'unidentified opportunity'}",
                rationale="The durable sales state shows an active opportunity. A governed follow-up may be appropriate if evidence and business policy support it.",
                confidence="MEDIUM", impact=float(amount) if isinstance(amount, (int, float)) else None,
                capability="sales.follow-up-due", resolution="EXECUTABLE_CANDIDATE", risk="MEDIUM",
                evidence=self._evidence("opportunities", item, i)))

        # Workforce pressure: reconciliation-required runs are advisory and must not be blindly retried.
        workforce = self._items("workforce")
        uncertain = [x for x in workforce if str(x.get("status", "")).upper() in {"PAUSED", "RECONCILIATION_REQUIRED"} or str(x.get("disposition", "")).upper() == "RECONCILIATION_REQUIRED"]
        for i, item in enumerate(uncertain[:5]):
            recommendations.append(self._recommendation(
                objective_id=objective_id, domain="workforce",
                title=f"Reconcile workforce run: {item.get('runId') or 'unidentified run'}",
                rationale="The durable workforce state indicates execution uncertainty. Verify the authoritative execution ledger/outbox before any retry or continuation.",
                confidence="HIGH", impact=None, capability="workforce.reconcile", resolution="GOVERNANCE_REVIEW_REQUIRED",
                risk="HIGH", evidence=self._evidence("workforce", item, i)))

        # Outcome learning signal: explicit negative outcomes become advisory lessons.
        outcomes = self._items("outcomes")
        negative = [x for x in outcomes if x.get("businessSuccess") is False]
        for i, item in enumerate(negative[:5]):
            recommendations.append(self._recommendation(
                objective_id=objective_id, domain="outcomes",
                title=f"Review unsuccessful outcome: {item.get('observationId') or 'unidentified observation'}",
                rationale="A durable outcome observation records business_success=false. Treat it as evidence for learning and strategy review, not as permission to change policy or execute a compensating action.",
                confidence="HIGH", impact=None, capability="outcome.learning-review", resolution="ADVISORY_ONLY",
                risk="LOW", evidence=self._evidence("outcomes", item, i)))

        # Explicitly expose unavailable intelligence rather than inventing capability.
        if not objectives:
            recommendations.append(self._recommendation(
                objective_id=None, domain="business",
                title="Define an active business objective",
                rationale="No active objective is present in the durable business operating state, so impact cannot be tied to a current business target.",
                confidence="HIGH", impact=None, capability="business-objective.create",
                resolution="CAPABILITY_UNAVAILABLE", risk="LOW", state=IntelligenceState.CAPABILITY_UNAVAILABLE,
                evidence=self._evidence("objectives", {}, 0)))

        # Deterministic dedupe by fingerprint.
        unique = {r.fingerprint: r for r in recommendations}
        ordered = sorted(unique.values(), key=lambda r: (r.domain, r.title, r.fingerprint))
        return {
            "schema": "BusinessIntelligenceDecisionSurface/v1",
            "version": self.VERSION,
            "generatedAt": self._now(),
            "workspaceId": self.workspace_id,
            "state": "LIVE" if self.truth else "UNKNOWN",
            "summary": {
                "recommendationCount": len(ordered),
                "advisory": sum(r.state == IntelligenceState.ADVISORY for r in ordered),
                "governanceReviewRequired": sum(r.state == IntelligenceState.GOVERNANCE_REVIEW_REQUIRED or r.capability_resolution == "GOVERNANCE_REVIEW_REQUIRED" for r in ordered),
                "capabilityUnavailable": sum(r.state == IntelligenceState.CAPABILITY_UNAVAILABLE or r.capability_resolution == "CAPABILITY_UNAVAILABLE" for r in ordered),
            },
            "recommendations": [r.as_dict() for r in ordered],
            "authority": {
                "readOnly": True,
                "actionAuthorityGranted": False,
                "recommendationsAreDecisions": False,
                "learningMayRewriteAuthority": False,
                "consequentialCommandsMustUse": ["KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION"],
            },
        }


def build_business_intelligence_decision_surface(workspace_id: str, truth: Mapping[str, TruthValue]) -> dict[str, Any]:
    return BusinessIntelligenceDecisionSurface(workspace_id, truth).build()
