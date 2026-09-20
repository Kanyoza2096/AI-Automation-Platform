"""Autonomy policies for the Reliability Engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Set

from services.reliability.models import AutonomyLevel, RemediationAction, REVERSIBLE_ACTIONS


@dataclass
class AutonomyPolicy:
    """
    Global + domain policy.

    Default production posture: Level 2 (reversible ops) for infrastructure,
    recommend-only for code/config changes.
    """

    global_level: AutonomyLevel = AutonomyLevel.REVERSIBLE
    domain_levels: Dict[str, AutonomyLevel] = field(default_factory=dict)
    denied_actions: Set[RemediationAction] = field(default_factory=set)
    max_auto_remediations_per_hour: int = 10

    def level_for(self, domain: str = "infrastructure") -> AutonomyLevel:
        return self.domain_levels.get(domain, self.global_level)

    def may_execute(self, action: RemediationAction, domain: str = "infrastructure") -> bool:
        if action in self.denied_actions:
            return False
        level = self.level_for(domain)
        order = list(AutonomyLevel)
        idx = order.index(level)

        if action == RemediationAction.ESCALATE_HUMAN:
            return True
        if action == RemediationAction.PROPOSE_PATCH:
            return idx >= order.index(AutonomyLevel.PATCH_PROPOSE)
        if action == RemediationAction.ROLLBACK_RELEASE:
            return idx >= order.index(AutonomyLevel.PLAYBOOK)
        if action == RemediationAction.DISABLE_CONNECTOR:
            return idx >= order.index(AutonomyLevel.PLAYBOOK)
        if action in REVERSIBLE_ACTIONS:
            return idx >= order.index(AutonomyLevel.REVERSIBLE)
        return idx >= order.index(AutonomyLevel.RECOMMEND)

    def requires_human(self, action: RemediationAction, domain: str = "infrastructure") -> bool:
        if action in (
            RemediationAction.PROPOSE_PATCH,
            RemediationAction.ROLLBACK_RELEASE,
            RemediationAction.DISABLE_CONNECTOR,
            RemediationAction.ESCALATE_HUMAN,
        ):
            return True
        if not self.may_execute(action, domain):
            return True
        return False


def load_policy_from_env() -> AutonomyPolicy:
    raw = (os.environ.get("RELIABILITY_AUTONOMY_LEVEL") or "2_reversible").strip().lower()
    mapping = {
        "0": AutonomyLevel.OBSERVE,
        "0_observe": AutonomyLevel.OBSERVE,
        "observe": AutonomyLevel.OBSERVE,
        "1": AutonomyLevel.RECOMMEND,
        "1_recommend": AutonomyLevel.RECOMMEND,
        "recommend": AutonomyLevel.RECOMMEND,
        "2": AutonomyLevel.REVERSIBLE,
        "2_reversible": AutonomyLevel.REVERSIBLE,
        "reversible": AutonomyLevel.REVERSIBLE,
        "3": AutonomyLevel.PLAYBOOK,
        "3_playbook": AutonomyLevel.PLAYBOOK,
        "playbook": AutonomyLevel.PLAYBOOK,
        "4": AutonomyLevel.PATCH_PROPOSE,
        "4_patch": AutonomyLevel.PATCH_PROPOSE,
        "5": AutonomyLevel.RESTRICTED_DEPLOY,
        "5_deploy": AutonomyLevel.RESTRICTED_DEPLOY,
    }
    level = mapping.get(raw, AutonomyLevel.REVERSIBLE)
    policy = AutonomyPolicy(global_level=level)
    policy.domain_levels = {
        "infrastructure": level,
        "publishing": AutonomyLevel.REVERSIBLE,
        "marketing": AutonomyLevel.RECOMMEND,
        "source_code": AutonomyLevel.PATCH_PROPOSE,
    }
    # Never auto-deploy by default
    if level != AutonomyLevel.RESTRICTED_DEPLOY:
        policy.denied_actions.add(RemediationAction.PROPOSE_PATCH)  # execute path still blocked
    return policy
