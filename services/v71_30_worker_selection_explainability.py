"""Phase 7.19 — Worker Selection Explainability & Decision Evidence.

Produces durable, tamper-evident evidence explaining a canonical worker-selection
result. It observes the canonical selection output and never authorizes, executes,
or calls providers. Explainability is evidence, not authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Optional
import hashlib, json, sqlite3

from services.v71_29_reputation_aware_worker_selection import ReputationAwareWorkerSelectionRegistry


class SelectionExplainabilityBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    SELECTION_MISSING = "selection_missing"
    SELECTION_TAMPERED = "selection_tampered"
    EVIDENCE_TAMPERED = "evidence_tampered"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    AUTHORITY_VIOLATION = "authority_violation"


@dataclass(frozen=True)
class WorkerCandidateDecisionEvidence:
    agent_id: str
    rank: int
    selected: bool
    total_score: float
    capability_score: float
    capacity_score: float
    safety_score: float
    autonomy_score: float
    budget_score: float
    reasons: tuple[str, ...]
    binding_factors: tuple[str, ...]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id, "rank": self.rank, "selected": self.selected,
            "total_score": self.total_score, "capability_score": self.capability_score,
            "capacity_score": self.capacity_score, "safety_score": self.safety_score,
            "autonomy_score": self.autonomy_score, "budget_score": self.budget_score,
            "reasons": list(self.reasons), "binding_factors": list(self.binding_factors),
        }


@dataclass(frozen=True)
class WorkerSelectionDecisionEvidence:
    evidence_id: str
    workspace_id: str
    selection_id: str
    task_id: str
    capability: str
    selected_agent_id: Optional[str]
    source_selection_fingerprint: str
    candidates: tuple[WorkerCandidateDecisionEvidence, ...]
    decision_summary: str
    binding_constraints: tuple[str, ...]
    advisory_only: bool = True
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False
    evidence_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "workspace_id": self.workspace_id,
            "selection_id": self.selection_id, "task_id": self.task_id,
            "capability": self.capability, "selected_agent_id": self.selected_agent_id,
            "source_selection_fingerprint": self.source_selection_fingerprint,
            "candidates": [c.canonical_payload() for c in self.candidates],
            "decision_summary": self.decision_summary,
            "binding_constraints": list(self.binding_constraints),
            "advisory_only": self.advisory_only, "authority_granted": self.authority_granted,
            "execution_requested": self.execution_requested, "provider_called": self.provider_called,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


class WorkerSelectionExplainabilityRegistry:
    VERSION = "phase7.19"

    def __init__(self, workspace_id: str, reputation_selection: ReputationAwareWorkerSelectionRegistry,
                 db_path: str = ":memory:"):
        ws = str(workspace_id or "").strip()
        if not ws or reputation_selection.workspace_id != ws:
            raise ValueError(SelectionExplainabilityBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self.reputation_selection = reputation_selection
        self._db = sqlite3.connect(db_path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS worker_selection_decision_evidence (
            evidence_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            selection_id TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL
        )""")
        self._db.commit()

    @staticmethod
    def _binding_factors(candidate: Any) -> tuple[str, ...]:
        factors = []
        for name, value in (("capability", candidate.capability_match),
                            ("capacity", candidate.capacity_score),
                            ("safety", candidate.safety_score),
                            ("autonomy", candidate.autonomy_score),
                            ("budget", candidate.budget_score)):
            if float(value) < 1.0:
                factors.append(f"{name}={float(value):.6f}")
        return tuple(factors)

    def explain(self, *, evidence_id: str, selection_id: str) -> WorkerSelectionDecisionEvidence:
        eid = str(evidence_id or "").strip()
        sid = str(selection_id or "").strip()
        if not eid or not sid:
            raise ValueError(SelectionExplainabilityBlock.INVALID)
        if self.get(eid) is not None or self._db.execute(
            "SELECT 1 FROM worker_selection_decision_evidence WHERE selection_id=?", (sid,)
        ).fetchone():
            raise ValueError(SelectionExplainabilityBlock.DUPLICATE)
        selection = self.reputation_selection.get(sid)
        if selection is None:
            raise ValueError(SelectionExplainabilityBlock.SELECTION_MISSING)
        self.reputation_selection.assert_integrity(selection)
        candidates = tuple(
            WorkerCandidateDecisionEvidence(
                agent_id=c.agent_id, rank=index + 1, selected=c.agent_id == selection.selected_agent_id,
                total_score=c.total_score, capability_score=c.capability_match,
                capacity_score=c.capacity_score, safety_score=c.safety_score,
                autonomy_score=c.autonomy_score, budget_score=c.budget_score,
                reasons=tuple(c.reasons), binding_factors=self._binding_factors(c),
            ) for index, c in enumerate(selection.candidates)
        )
        binding = []
        if candidates:
            selected = candidates[0]
            binding.extend(selected.binding_factors)
            summary = f"Selected {selection.selected_agent_id} as rank 1 from {len(candidates)} eligible candidate(s); canonical eligibility preceded reputation ranking."
        else:
            summary = "No eligible worker was returned by the canonical selection engine; reputation could not create eligibility."
            binding.append("no_eligible_worker")
        rec = WorkerSelectionDecisionEvidence(
            eid, self.workspace_id, selection.selection_id, selection.task_id, selection.capability,
            selection.selected_agent_id, selection.selection_fingerprint, candidates, summary,
            tuple(binding), True, False, False, False, ""
        )
        rec = WorkerSelectionDecisionEvidence(**{**rec.__dict__, "evidence_fingerprint": rec.fingerprint()})
        self._db.execute("INSERT INTO worker_selection_decision_evidence VALUES (?,?,?,?,?)", (
            rec.evidence_id, rec.workspace_id, rec.selection_id,
            json.dumps(rec.canonical_payload(), sort_keys=True, default=str), rec.evidence_fingerprint,
        ))
        self._db.commit()
        return rec

    def get(self, evidence_id: str) -> Optional[WorkerSelectionDecisionEvidence]:
        row = self._db.execute("SELECT payload, fingerprint FROM worker_selection_decision_evidence WHERE evidence_id=?", (str(evidence_id),)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["candidates"] = tuple(WorkerCandidateDecisionEvidence(**c) for c in payload["candidates"])
        payload["binding_constraints"] = tuple(payload["binding_constraints"])
        payload["evidence_fingerprint"] = row[1]
        return WorkerSelectionDecisionEvidence(**payload)

    def assert_integrity(self, evidence: WorkerSelectionDecisionEvidence) -> None:
        if evidence.workspace_id != self.workspace_id:
            raise ValueError(SelectionExplainabilityBlock.TENANT_MISMATCH)
        if (not evidence.advisory_only or evidence.authority_granted or
            evidence.execution_requested or evidence.provider_called):
            raise ValueError(SelectionExplainabilityBlock.AUTHORITY_VIOLATION)
        if evidence.evidence_fingerprint != evidence.fingerprint():
            raise ValueError(SelectionExplainabilityBlock.EVIDENCE_TAMPERED)
        stored = self.get(evidence.evidence_id)
        if stored is None or stored.evidence_fingerprint != evidence.evidence_fingerprint:
            raise ValueError(SelectionExplainabilityBlock.EVIDENCE_TAMPERED)
        selection = self.reputation_selection.get(evidence.selection_id)
        if selection is None:
            raise ValueError(SelectionExplainabilityBlock.SELECTION_MISSING)
        self.reputation_selection.assert_integrity(selection)
        if selection.selection_fingerprint != evidence.source_selection_fingerprint:
            raise ValueError(SelectionExplainabilityBlock.SELECTION_TAMPERED)

    def context(self, evidence_id: str) -> Mapping[str, Any]:
        evidence = self.get(evidence_id)
        if evidence is None:
            raise ValueError(SelectionExplainabilityBlock.SELECTION_MISSING)
        self.assert_integrity(evidence)
        return {
            "evidence_id": evidence.evidence_id,
            "selection_id": evidence.selection_id,
            "selected_agent_id": evidence.selected_agent_id,
            "binding_constraints": evidence.binding_constraints,
            "advisory_only": True,
            "authority_granted": False,
            "execution_requested": False,
        }

    def close(self) -> None:
        self._db.close()


__all__ = [
    "SelectionExplainabilityBlock", "WorkerCandidateDecisionEvidence",
    "WorkerSelectionDecisionEvidence", "WorkerSelectionExplainabilityRegistry",
]
