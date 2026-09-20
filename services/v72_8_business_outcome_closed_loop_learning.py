"""V72.8 — Business Outcome Intelligence & Closed-Loop Learning.

Aggregates observed/associated outcome-attribution evidence into durable,
advisory learning signals. Learning never grants authority or rewrites policy.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json
from typing import Any, Mapping, Sequence


def _fp(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _id(prefix: str, value: Any) -> str:
    return f"kdo:{prefix}:v72.8:" + _fp(value)[:32]


class LearningState:
    LEARNED_PATTERN = "LEARNED_PATTERN"
    MIXED_EVIDENCE = "MIXED_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LearningSignal:
    signal_id: str
    workspace_id: str
    pattern_key: str
    state: str
    confidence: str
    sample_size: int
    observed_successes: int
    observed_failures: int
    associated_outcomes: int
    evidence: tuple[Mapping[str, Any], ...]
    recommendation_ids: tuple[str, ...]
    objective_id: str | None
    advisory: str
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "signalId": self.signal_id, "workspaceId": self.workspace_id,
            "patternKey": self.pattern_key, "state": self.state,
            "confidence": self.confidence, "sampleSize": self.sample_size,
            "observedSuccesses": self.observed_successes, "observedFailures": self.observed_failures,
            "associatedOutcomes": self.associated_outcomes,
            "recommendationIds": list(self.recommendation_ids), "objectiveId": self.objective_id,
            "evidence": [dict(x) for x in self.evidence], "advisory": self.advisory,
            "fingerprint": self.fingerprint,
            "actionAuthorityGranted": False, "policyMutationAllowed": False,
            "learningMayRewriteAuthority": False,
        }


class BusinessOutcomeClosedLoopLearning:
    VERSION = "v72.8"

    def __init__(self, workspace_id: str, attributions: Sequence[Mapping[str, Any]]):
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        self.attributions = [dict(x) for x in attributions]

    @staticmethod
    def _value(row: Mapping[str, Any], *keys: str) -> Any:
        for k in keys:
            if row.get(k) is not None:
                return row[k]
        return None

    def _group(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for a in self.attributions:
            rid = str(self._value(a, "recommendationId", "recommendation_id") or "")
            if not rid:
                continue
            pattern = str(self._value(a, "patternKey", "pattern_key", "objectiveId", "objective_id", "domain") or "unclassified")
            groups.setdefault(pattern, []).append(a)
        return groups

    def _signal(self, pattern: str, rows: list[dict[str, Any]]) -> LearningSignal:
        successes = failures = associated = 0
        recs: set[str] = set()
        evidence: list[Mapping[str, Any]] = []
        objective = None
        for row in rows:
            state = str(self._value(row, "state") or "INSUFFICIENT_EVIDENCE")
            rid = str(self._value(row, "recommendationId", "recommendation_id") or "")
            oid = str(self._value(row, "outcomeId", "outcome_id") or "")
            recs.add(rid)
            if self._value(row, "objectiveId", "objective_id") is not None:
                objective = str(self._value(row, "objectiveId", "objective_id"))
            if state == "ASSOCIATED": associated += 1
            if state == "OBSERVED":
                success = self._value(row, "businessSuccess", "success", "outcomeSuccess")
                if success is True: successes += 1
                elif success is False: failures += 1
            if oid:
                evidence.append({"source": "outcomeAttribution", "recommendationId": rid, "outcomeId": oid, "fingerprint": _fp(row)})
        sample = successes + failures
        if sample >= 3 and (successes == sample or failures == sample):
            state = LearningState.LEARNED_PATTERN
            confidence = "HIGH" if sample >= 5 else "MEDIUM"
            advisory = "Repeated observed outcomes support a business pattern; use it as advisory evidence only."
        elif sample >= 2 or associated:
            state = LearningState.MIXED_EVIDENCE
            confidence = "MEDIUM" if sample >= 2 else "LOW"
            advisory = "Evidence is suggestive but mixed or observational; do not infer causality."
        else:
            state = LearningState.INSUFFICIENT_EVIDENCE
            confidence = "LOW"
            advisory = "There is insufficient outcome evidence to learn a reliable pattern."
        canonical = {"workspaceId": self.workspace_id, "patternKey": pattern, "rows": sorted((_fp(x) for x in rows))}
        fp = _fp(canonical)
        return LearningSignal(_id("learning", canonical), self.workspace_id, pattern, state, confidence, sample,
                              successes, failures, associated, tuple(evidence[:25]), tuple(sorted(recs)), objective, advisory, fp)

    def build(self) -> dict[str, Any]:
        signals = [self._signal(k, v) for k, v in self._group().items()]
        signals.sort(key=lambda s: s.signal_id)
        return {
            "schema": "BusinessOutcomeClosedLoopLearning/v1", "version": self.VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(), "workspaceId": self.workspace_id,
            "signals": [s.as_dict() for s in signals],
            "summary": {"signals": len(signals), "learnedPatterns": sum(s.state == LearningState.LEARNED_PATTERN for s in signals),
                        "mixedEvidence": sum(s.state == LearningState.MIXED_EVIDENCE for s in signals),
                        "insufficientEvidence": sum(s.state == LearningState.INSUFFICIENT_EVIDENCE for s in signals)},
            "authority": {"readOnly": True, "advisoryOnly": True, "actionAuthorityGranted": False,
                          "policyMutationAllowed": False, "learningMayRewriteAuthority": False,
                          "consequentialCommandsMustUse": ["KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX", "CONNECTOR_PROVIDER", "RECONCILIATION"]},
        }
