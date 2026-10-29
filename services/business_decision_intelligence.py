"""Phase 4 — canonical Business Decision Intelligence read model.

This service converges KDO's existing decision candidate, policy evaluation,
recommendation, evidence, drift and audit primitives into an auditable decision
record. It does not create a second decision engine and never grants execution
authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from models.business_decision_intelligence import (
    BusinessDecisionIntelligence,
    DecisionDisposition,
    DecisionRecord,
    DecisionRisk,
    SCHEMA,
)


def _stable_id(workspace_id: str, *parts: object) -> str:
    raw = json.dumps([str(x) for x in parts], sort_keys=True, separators=(",", ":"), default=str)
    return "decision_" + hashlib.sha256(f"{workspace_id}|{raw}".encode()).hexdigest()[:24]


class BusinessDecisionIntelligenceProjector:
    """Project authoritative decision-plane artifacts into one read model."""

    def project(
        self,
        *,
        workspace_id: str,
        candidates: Iterable[Mapping[str, Any]] = (),
        evaluations: Iterable[Mapping[str, Any]] = (),
        recommendations: Iterable[Mapping[str, Any]] = (),
        audit_records: Iterable[Mapping[str, Any]] = (),
        drift_snapshot: Any | None = None,
        evidence_graph: Any | None = None,
    ) -> BusinessDecisionIntelligence:
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError("workspace_id is required")

        warnings: list[str] = []
        by_candidate: dict[str, Mapping[str, Any]] = {}
        by_evaluation: dict[str, Mapping[str, Any]] = {}
        by_recommendation: dict[str, Mapping[str, Any]] = {}
        by_audit: dict[str, Mapping[str, Any]] = {}

        def accept(target: dict[str, Mapping[str, Any]], row: Mapping[str, Any], key: str, label: str) -> None:
            if str(row.get("workspace_id") or "") != ws:
                warnings.append(f"{label}_workspace_mismatch:{row.get(key, 'unknown')}")
                return
            value = str(row.get(key) or "").strip()
            if not value:
                warnings.append(f"{label}_missing_id")
                return
            target[value] = row

        for row in candidates:
            accept(by_candidate, row, "candidate_id", "candidate")
        for row in evaluations:
            accept(by_evaluation, row, "evaluation_id", "evaluation")
        for row in recommendations:
            accept(by_recommendation, row, "recommendation_id", "recommendation")
        for row in audit_records:
            accept(by_audit, row, "audit_id", "audit")

        drift_refs = self._drift_refs(drift_snapshot, ws, warnings)
        decisions: list[DecisionRecord] = []

        # Evaluation is the authoritative disposition source. Candidates alone
        # can describe an option, but never become a decision.
        for evaluation_id, evaluation in sorted(by_evaluation.items()):
            candidate_id = str(evaluation.get("candidate_id") or "")
            candidate = by_candidate.get(candidate_id)
            if candidate is None:
                warnings.append(f"evaluation_missing_candidate:{evaluation_id}")
                continue

            recommendation = self._recommendation_for(evaluation, by_recommendation)
            audit = self._audit_for(evaluation, by_audit)
            disposition = self._disposition(evaluation)
            risk = self._risk(evaluation.get("risk_level"))
            confidence = self._confidence(candidate, evaluation, recommendation)
            evidence_refs = self._evidence_refs(evidence_graph, evaluation, recommendation, ws)
            option_refs = [candidate_id]
            decision_id = _stable_id(ws, evaluation_id, evaluation.get("policy_version", ""), candidate_id)
            action = str(evaluation.get("action") or candidate.get("candidate_action") or "").strip() or None

            record = DecisionRecord(
                decision_id=decision_id,
                workspace_id=ws,
                objective_id=evaluation.get("objective_id") or candidate.get("objective_id"),
                question=str(candidate.get("intent") or ""),
                disposition=disposition,
                recommendation=self._recommendation_text(recommendation, action, disposition),
                rationale=str((recommendation or {}).get("rationale") or evaluation.get("reason") or candidate.get("reason") or ""),
                risk=risk,
                confidence=confidence,
                evidence_refs=evidence_refs,
                constraint_refs=[str(x) for x in list(evaluation.get("constraints") or [])[:20]],
                drift_refs=list(drift_refs),
                option_refs=option_refs,
                candidate_id=candidate_id,
                evaluation_id=evaluation_id,
                recommendation_id=str(recommendation.get("recommendation_id")) if recommendation else None,
                audit_id=str(audit.get("audit_id")) if audit else None,
                action_type=action,
                action_authority_granted=False,
                governance_reason=str(evaluation.get("reason") or ""),
                provenance={
                    "candidate_schema": candidate.get("schema_version"),
                    "evaluation_policy_version": evaluation.get("policy_version"),
                    "audit_fingerprint": audit.get("evaluation_fingerprint") if audit else None,
                    "source": "business_decision_policy",
                },
            )
            decisions.append(record)

        if not by_evaluation and by_candidate:
            warnings.append("candidates_without_policy_evaluation")
        if not decisions:
            warnings.append("no_decisions")

        decisions.sort(key=lambda d: d.decision_id)
        coverage = {
            "candidates": len(by_candidate),
            "evaluations": len(by_evaluation),
            "recommendations": len(by_recommendation),
            "audits": len(by_audit),
            "decisions": len(decisions),
            "drift_refs": len(drift_refs),
            "dispositions": {d.value: sum(x.disposition == d for x in decisions) for d in DecisionDisposition},
        }
        return BusinessDecisionIntelligence(schema_version=SCHEMA, workspace_id=ws, decisions=decisions, coverage=coverage, warnings=sorted(set(warnings)))

    @staticmethod
    def _recommendation_for(evaluation: Mapping[str, Any], recommendations: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
        rid = str(evaluation.get("recommendation_id") or "")
        if rid and rid in recommendations:
            return recommendations[rid]
        action = str(evaluation.get("action") or "")
        matches = [r for r in recommendations.values() if str(r.get("capability") or r.get("action") or "") == action]
        return sorted(matches, key=lambda r: str(r.get("recommendation_id") or ""))[0] if matches else None

    @staticmethod
    def _audit_for(evaluation: Mapping[str, Any], audits: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
        eid = str(evaluation.get("evaluation_id") or "")
        matches = [a for a in audits.values() if str(a.get("evaluation_id") or "") == eid]
        return sorted(matches, key=lambda a: str(a.get("audit_id") or ""))[0] if matches else None

    @staticmethod
    def _disposition(evaluation: Mapping[str, Any]) -> DecisionDisposition:
        value = str(evaluation.get("disposition") or "BLOCK").upper()
        return {
            "ALLOW": DecisionDisposition.RECOMMEND,
            "APPROVAL_REQUIRED": DecisionDisposition.APPROVAL_REQUIRED,
            "REVIEW": DecisionDisposition.REVIEW,
            "BLOCK": DecisionDisposition.BLOCK,
        }.get(value, DecisionDisposition.NO_DECISION)

    @staticmethod
    def _risk(value: Any) -> DecisionRisk:
        value = str(value or "unknown").lower()
        return DecisionRisk(value) if value in {x.value for x in DecisionRisk} else DecisionRisk.UNKNOWN

    @staticmethod
    def _confidence(candidate: Mapping[str, Any], evaluation: Mapping[str, Any], recommendation: Mapping[str, Any] | None) -> float:
        values = [float(candidate.get("intent_confidence") or 0.0), float(candidate.get("governance_score") or 0.0)]
        if recommendation:
            raw = recommendation.get("confidence")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
            elif str(raw).lower() in {"high", "very_high"}:
                values.append(0.9)
            elif str(raw).lower() == "medium":
                values.append(0.65)
            elif str(raw).lower() == "low":
                values.append(0.35)
        return round(sum(values) / len(values), 6) if values else 0.0

    @staticmethod
    def _recommendation_text(recommendation: Mapping[str, Any] | None, action: str | None, disposition: DecisionDisposition) -> str:
        if recommendation and recommendation.get("title"):
            return str(recommendation["title"])
        if disposition == DecisionDisposition.RECOMMEND and action:
            return f"Proceed with governed action: {action}"
        if disposition == DecisionDisposition.APPROVAL_REQUIRED:
            return "Obtain required approval before proceeding."
        if disposition == DecisionDisposition.REVIEW:
            return "Route to human/governance review before proceeding."
        if disposition == DecisionDisposition.BLOCK:
            return "Do not proceed; the decision policy blocked this candidate."
        return "No actionable decision established."

    @staticmethod
    def _drift_refs(snapshot: Any | None, workspace_id: str, warnings: list[str]) -> list[str]:
        if snapshot is None:
            return []
        if getattr(snapshot, "workspace_id", workspace_id) != workspace_id:
            warnings.append("drift_workspace_mismatch")
            return []
        refs: list[str] = []
        for item in getattr(snapshot, "findings", ()):
            finding = getattr(item, "finding", None)
            health = getattr(finding, "health", None)
            value = getattr(health, "value", health)
            if value == "drifted":
                resource = getattr(item, "resource", None)
                ref = getattr(resource, "resource_ref", None)
                if ref:
                    refs.append(str(ref))
        return sorted(set(refs))

    @staticmethod
    def _evidence_refs(graph: Any | None, evaluation: Mapping[str, Any], recommendation: Mapping[str, Any] | None, workspace_id: str) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for source, value in (("evaluation", evaluation.get("evaluation_id")), ("recommendation", recommendation.get("recommendation_id") if recommendation else None)):
            if value:
                refs.append({"type": source, "id": str(value)})
        if graph is not None:
            snapshot = graph.snapshot()
            for node in snapshot.get("nodes", []):
                if node.get("workspace_id") != workspace_id or node.get("node_type") != "decision":
                    continue
                if node.get("payload", {}).get("evaluation_id") == evaluation.get("evaluation_id"):
                    refs.append({"type": "evidence_node", "id": node.get("node_id"), "fingerprint": node.get("fingerprint")})
        return refs[:20]


__all__ = ["BusinessDecisionIntelligenceProjector", "SCHEMA"]
