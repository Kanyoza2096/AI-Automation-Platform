"""Phase 7.18 — Reputation-Aware Worker Selection.

Reputation/capability evidence is a bounded advisory input to worker selection.
It may rank already-eligible workers, but it can never make an ineligible worker
eligible and can never grant authority, execution, provider access, or bypass
capacity, safety, permission, scope, budget, lifecycle, or governance fences.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Optional, Any
import hashlib, json

from services.v71_9_worker_selection import (
    WorkerSelectionRegistry, WorkerSelection, WorkerCandidate, SelectionBlock,
)
from services.v71_31_worker_selection_policy import WorkerSelectionPolicyRegistry, WorkerSelectionPolicyProfile, SelectionPolicyBlock
from services.v71_28_worker_reputation_capability_evidence import (
    WorkerReputationCapabilityRegistry, ReputationBlock,
)
from services.v71_35_worker_capability_qualification import (
    WorkerCapabilityQualificationRegistry, QualificationBlock,
)


class ReputationSelectionBlock(str):
    TENANT_MISMATCH = "tenant_mismatch"
    REPUTATION_TAMPERED = "reputation_tampered"
    SELECTION_TAMPERED = "selection_tampered"
    INVALID_POLICY = "invalid_policy"
    QUALIFICATION_TAMPERED = "qualification_tampered"


@dataclass(frozen=True)
class ReputationSelectionPolicy:
    max_influence: float = 0.05
    minimum_samples_for_full_signal: int = 5
    require_verified_samples: bool = True

    def validate(self) -> None:
        if not 0.0 <= float(self.max_influence) <= 0.10:
            raise ValueError(ReputationSelectionBlock.INVALID_POLICY)
        if int(self.minimum_samples_for_full_signal) < 1:
            raise ValueError(ReputationSelectionBlock.INVALID_POLICY)


class ReputationAwareWorkerSelectionRegistry:
    """Adds reputation only after canonical eligibility has already passed."""

    VERSION = "phase7.18"

    def __init__(self, workspace_id: str, base_selection: WorkerSelectionRegistry,
                 reputation: WorkerReputationCapabilityRegistry,
                 policy: Optional[ReputationSelectionPolicy] = None,
                 policy_registry: Optional[WorkerSelectionPolicyRegistry] = None,
                 policy_id: Optional[str] = None,
                 policy_activation: Any = None,
                 qualification: Optional[WorkerCapabilityQualificationRegistry] = None):
        ws = str(workspace_id or "").strip()
        if not ws or base_selection.workspace_id != ws or reputation.workspace_id != ws:
            raise ValueError(ReputationSelectionBlock.TENANT_MISMATCH)
        self.workspace_id = ws
        self.base_selection = base_selection
        self.reputation = reputation
        self.policy_registry = policy_registry
        self.policy_id = policy_id
        self.policy_activation = policy_activation
        self.qualification = qualification
        if qualification is not None and qualification.workspace_id != ws:
            raise ValueError(ReputationSelectionBlock.TENANT_MISMATCH)
        self.policy = policy or ReputationSelectionPolicy()
        if policy_registry is not None:
            if policy_registry.workspace_id != ws:
                raise ValueError(ReputationSelectionBlock.TENANT_MISMATCH)
            if not policy_id:
                raise ValueError(ReputationSelectionBlock.INVALID_POLICY)
            profile = policy_registry.get(policy_id)
            if profile is None:
                raise ValueError("policy_missing")
            policy_registry.assert_integrity(profile)
            self.policy = ReputationSelectionPolicy(profile.reputation_influence, profile.minimum_reputation_samples, profile.require_verified_reputation_samples)
        self.policy.validate()
        self._selections: dict[str, WorkerSelection] = {}

    def select(self, *, selection_id: str, task_id: str, capability: str,
               requested_scope: Optional[Mapping[str, Any]] = None,
               candidate_agent_ids: Optional[tuple[str, ...]] = None,
               minimum_autonomy_level: int = 0,
               budget_ids: Optional[Mapping[str, str]] = None) -> WorkerSelection:
        if self.policy_activation is not None:
            if not self.policy_id:
                raise ValueError(ReputationSelectionBlock.INVALID_POLICY)
            self.policy_activation.enforce(policy_id=self.policy_id, capability=capability)
        # Qualification is an eligibility gate, not a ranking signal.
        # Filter before canonical selection so an unqualified worker can never
        # win merely because it scores well on other dimensions.
        ids = tuple(candidate_agent_ids) if candidate_agent_ids is not None else tuple(
            a.agent_id for a in self.base_selection.identity_registry.list_agents()
        )
        if self.qualification is not None:
            qualified_ids = []
            for aid in sorted(set(str(x) for x in ids)):
                q = self.qualification.get_for_worker(aid, capability)
                if q is None:
                    continue
                try:
                    self.qualification.assert_integrity(q)
                except ValueError as exc:
                    if str(exc) == QualificationBlock.QUALIFICATION_TAMPERED.value:
                        raise ValueError(ReputationSelectionBlock.QUALIFICATION_TAMPERED.value)
                    continue
                if self.qualification.is_currently_qualified(aid, capability):
                    qualified_ids.append(aid)
            ids = tuple(qualified_ids)
        base = self.base_selection.select(
            selection_id=selection_id, task_id=task_id, capability=capability,
            requested_scope=requested_scope, candidate_agent_ids=ids,
            minimum_autonomy_level=(self.policy_registry.get(self.policy_id).minimum_autonomy_level if self.policy_registry is not None else minimum_autonomy_level),
            budget_ids=budget_ids,
            selection_policy=({"capability": self.policy_registry.get(self.policy_id).capability_weight, "capacity": self.policy_registry.get(self.policy_id).capacity_weight, "safety": self.policy_registry.get(self.policy_id).safety_weight, "autonomy": self.policy_registry.get(self.policy_id).autonomy_weight, "budget": self.policy_registry.get(self.policy_id).budget_weight} if self.policy_registry is not None else None),
        )
        ranked: list[WorkerCandidate] = []
        for candidate in base.candidates:
            profile = self.reputation.profile(worker_id=candidate.agent_id, capability=capability)
            if profile.sample_count == 0:
                signal = 0.0
            else:
                if self.policy.require_verified_samples and profile.verified_sample_count <= 0:
                    signal = 0.0
                else:
                    confidence = min(1.0, profile.sample_count / self.policy.minimum_samples_for_full_signal)
                    quality = (profile.average_capability_score + profile.average_performance_score) / 2.0
                    signal = round(confidence * quality, 6)
            influence = float(self.policy.max_influence)
            adjusted = round(candidate.total_score * (1.0 - influence + influence * signal), 6)
            reason = candidate.reasons + (f"reputation_signal={signal:.6f}",)
            ranked.append(WorkerCandidate(
                candidate.agent_id, candidate.capability_match, candidate.capacity_score,
                candidate.safety_score, candidate.autonomy_score, candidate.budget_score,
                adjusted, reason,
            ))
        ranked.sort(key=lambda c: (-c.total_score, c.agent_id))
        selected = ranked[0].agent_id if ranked else None
        result = WorkerSelection(
            base.selection_id, base.workspace_id, base.task_id, base.capability,
            base.requested_scope, selected, tuple(ranked), True, False, False, ""
        )
        result = WorkerSelection(**{**result.__dict__, "selection_fingerprint": result.fingerprint()})
        self._selections[selection_id] = result
        return result

    def get(self, selection_id: str) -> Optional[WorkerSelection]:
        return self._selections.get(str(selection_id))

    def assert_integrity(self, selection: WorkerSelection) -> None:
        if selection.workspace_id != self.workspace_id:
            raise ValueError(ReputationSelectionBlock.TENANT_MISMATCH)
        if selection.selection_fingerprint != selection.fingerprint():
            raise ValueError(ReputationSelectionBlock.SELECTION_TAMPERED)
        for candidate in selection.candidates:
            profile = self.reputation.profile(worker_id=candidate.agent_id, capability=selection.capability)
            if profile.can_authorize or profile.can_execute or profile.can_call_provider:
                raise ValueError(ReputationSelectionBlock.REPUTATION_TAMPERED)

    def context(self, selection_id: str) -> Mapping[str, Any]:
        selection = self.get(selection_id)
        if selection is None:
            raise ValueError(SelectionBlock.NO_ELIGIBLE_WORKER.value)
        self.assert_integrity(selection)
        return {
            "selection_id": selection.selection_id,
            "selected_agent_id": selection.selected_agent_id,
            "candidate_count": len(selection.candidates),
            "reputation_aware": True,
            "qualification_aware": self.qualification is not None,
            "advisory_only": True,
            "authority_granted": False,
            "execution_requested": False,
        }


__all__ = [
    "ReputationSelectionPolicy", "ReputationAwareWorkerSelectionRegistry",
    "ReputationSelectionBlock",
]
