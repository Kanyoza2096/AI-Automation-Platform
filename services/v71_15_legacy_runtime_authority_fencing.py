"""V71.15 — Legacy runtime fencing and authority convergence.

This module does not delete the legacy Kanyoza engines. It makes their authority
explicit: legacy engines may observe, interpret, and propose, but consequential
business side effects must enter the canonical V70/V71 governance + Action Kernel
path. A legacy direct side-effect capability is therefore fenced closed unless a
future canonical adapter explicitly proves it is operating inside the governed
path.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
import hashlib, json, logging

logger = logging.getLogger("kanyoza.v71.15")

class AuthorityMode(str, Enum):
    OBSERVE = "observe"
    INTERPRET = "interpret"
    PROPOSE = "propose"
    GOVERN = "govern"
    EXECUTE = "execute"
    RECONCILE = "reconcile"

@dataclass(frozen=True)
class RuntimeAuthority:
    component: str
    modes: tuple[AuthorityMode, ...]
    canonical: bool
    notes: str = ""

LEGACY_DIRECT_SIDE_EFFECTS = frozenset({
    "publish_scheduled_content",
    "optimize_content_schedule",
    "queue_brand_revival",
    "restart_failed_service",
    "quarantine_security_issue",
    "engagement_reply",
    "engagement_like",
    "growth_invite",
    "growth_cross_engage",
    "news_publish",
})

AUTHORITY_MAP = (
    RuntimeAuthority("Meta Engine", (AuthorityMode.OBSERVE, AuthorityMode.INTERPRET), True,
                     "Health/diagnosis/telemetry; recovery recommendations do not bypass governance."),
    RuntimeAuthority("EventBus", (AuthorityMode.OBSERVE,), True,
                     "Transport only; it does not grant authority or execute providers."),
    RuntimeAuthority("ConditionEngine", (AuthorityMode.OBSERVE, AuthorityMode.INTERPRET, AuthorityMode.PROPOSE), True,
                     "Decision intelligence; consequential decisions must resolve through governance."),
    RuntimeAuthority("Director", (AuthorityMode.OBSERVE, AuthorityMode.INTERPRET, AuthorityMode.PROPOSE), False,
                     "Legacy autonomous brain is fenced from consequential direct execution."),
    RuntimeAuthority("AutonomousLoop", (AuthorityMode.OBSERVE, AuthorityMode.INTERPRET, AuthorityMode.PROPOSE), False,
                     "Legacy loop may emit proposals/events; publish execution is canonicalized."),
    RuntimeAuthority("Schedulers", (AuthorityMode.OBSERVE, AuthorityMode.PROPOSE), False,
                     "Scheduling may create governed workflow intents; direct provider side effects are fenced."),
    RuntimeAuthority("V69 Workflow Execution", (AuthorityMode.GOVERN, AuthorityMode.PROPOSE, AuthorityMode.RECONCILE), True,
                     "Durable workflow orchestration; side effects remain outside the workflow plane."),
    RuntimeAuthority("V69 Connector Runtime Gateway", (AuthorityMode.GOVERN, AuthorityMode.EXECUTE), True,
                     "Final connector capability gate before governed connector execution."),
    RuntimeAuthority("V70 Business Operating Layer", (AuthorityMode.INTERPRET, AuthorityMode.PROPOSE, AuthorityMode.GOVERN), True,
                     "Canonical business-domain orchestration and workflow admission."),
    RuntimeAuthority("V71 Workforce", (AuthorityMode.INTERPRET, AuthorityMode.PROPOSE, AuthorityMode.GOVERN), True,
                     "Identity/permissions/planning/budgets/takeover/coordination/admission; no second executor."),
    RuntimeAuthority("KCPG Gateway", (AuthorityMode.OBSERVE, AuthorityMode.GOVERN), True,
                     "Client authority boundary and BFF; consequential commands enter canonical governance."),
    RuntimeAuthority("Action Kernel", (AuthorityMode.EXECUTE,), True,
                     "Canonical action execution authority and durable outbox boundary."),
    RuntimeAuthority("Durable Outbox / Workers", (AuthorityMode.EXECUTE, AuthorityMode.RECONCILE), True,
                     "Durable delivery, provider execution and authoritative result capture."),
    RuntimeAuthority("Reconciliation / Outcome / Evidence", (AuthorityMode.RECONCILE, AuthorityMode.OBSERVE), True,
                     "Provider result reconciliation and business outcome/evidence feedback."),
)

def legacy_side_effect_allowed(capability: str) -> bool:
    """Process-wide fail-closed check for legacy direct side-effect helpers."""
    return str(capability or "").strip() not in LEGACY_DIRECT_SIDE_EFFECTS

class LegacyRuntimeAuthorityFence:
    VERSION = "v71.15"
    def __init__(self, *, production: bool = True):
        self.production = production
        self._blocked: dict[str, int] = {}
        self._fingerprint = self._calc_fingerprint()

    def _calc_fingerprint(self) -> str:
        material = json.dumps([
            {"component": x.component, "modes": [m.value for m in x.modes], "canonical": x.canonical, "notes": x.notes}
            for x in AUTHORITY_MAP
        ], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()

    def classify(self, component: str) -> RuntimeAuthority:
        for item in AUTHORITY_MAP:
            if item.component == component:
                return item
        raise KeyError(f"unknown_runtime_component:{component}")

    def allows_legacy_execution(self, capability: str) -> bool:
        """Allow safe observation/diagnostic capabilities; fence side effects.

        Production fencing is scoped to the legacy *consequential* capability
        set. A blanket ``return not self.production`` would also disable safe
        health/diagnostic work and would make Director ineffective rather than
        merely non-authoritative.
        """
        cap = str(capability or "").strip()
        if cap in LEGACY_DIRECT_SIDE_EFFECTS:
            self._blocked[cap] = self._blocked.get(cap, 0) + 1
            return False
        return True

    def fence_result(self, *, component: str, capability: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._blocked[str(capability)] = self._blocked.get(str(capability), 0) + 1
        return {
            "ok": False,
            "status": "fenced",
            "disposition": "CAPABILITY_UNAVAILABLE",
            "component": component,
            "capability": str(capability),
            "reason": "legacy_consequential_execution_requires_canonical_v70_v71_governance",
            "execution_requested": False,
            "authority_granted": False,
            "params": dict(params or {}),
            "canonical_next_path": "Business Operating Layer -> V71 Workforce -> Governance -> Action Kernel -> durable outbox",
        }

    def status(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "production_fence": self.production,
            "parallel_authority_plane": False,
            "authority_map": [
                {"component": x.component, "modes": [m.value for m in x.modes], "canonical": x.canonical, "notes": x.notes}
                for x in AUTHORITY_MAP
            ],
            "legacy_direct_side_effects": sorted(LEGACY_DIRECT_SIDE_EFFECTS),
            "blocked_counts": dict(self._blocked),
            "binding_fingerprint": self._fingerprint,
            "canonical_execution_law": "KCPG -> Business Objective -> V70 -> V71 Workforce -> Governance -> Action Kernel -> durable outbox -> Connector -> Provider -> Reconciliation",
        }

def install_legacy_runtime_fencing(app: Any, container: Any) -> LegacyRuntimeAuthorityFence:
    fence = LegacyRuntimeAuthorityFence(production=True)
    app.state.legacy_runtime_authority_fence = fence
    try:
        director = None
        from services.director import get_director
        director = __import__("asyncio").run(get_director()) if False else None
        # Director is initialized during lifecycle boot; main installs this after boot.
        from services import director as director_module
        director = getattr(director_module, "director", None)
        if director is not None and hasattr(director, "set_authority_fence"):
            director.set_authority_fence(fence)
    except Exception as exc:
        logger.warning("V71.15 Director fence installation deferred/failed: %s", exc)
    logger.info("V71.15 legacy runtime authority fencing installed")
    return fence
