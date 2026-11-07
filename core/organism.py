"""
Phase C — Organism (one living company per workspace).

Species services (Meta infra, Reflex law, worker heartbeats) stay global.
Everything that thinks *as a tenant* is scoped here:

  - DemocracyEngine (Council) with persona set + config
  - MemorySystem
  - DreamSystem (workspace timezone quiet hours)
  - Pulse thresholds (activity expectations)
  - DecisionGate slice (workspace-local view)
  - Optional Director handle when available
  - Enabled industry organs

OrganismRegistry is the species census: get-or-create by workspace_id,
never share mutable mind state across workspaces.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("kanyoza.core.organism")


# ── Persona catalogs by industry organ ────────────────────────────────────

CORE_PERSONAS = [
    "youth_advocate",
    "business_owner",
    "community_elder",
    "data_analyst",
    "risk_manager",
    "trend_spotter",
    "customer_voice",
    "gut_feeling",
    "mood_ring",
    "rippler",
    "affiliate_marketer",
    "theologian",
]

INDUSTRY_PERSONAS: Dict[str, List[str]] = {
    "church_mis": ["theologian", "community_elder", "customer_voice"],
    "hospital": ["risk_manager", "data_analyst", "customer_voice"],
    "school_mis": ["community_elder", "youth_advocate", "data_analyst"],
    "crm": ["business_owner", "customer_voice", "affiliate_marketer"],
    "default": ["business_owner", "trend_spotter", "customer_voice"],
}


@dataclass
class PulseThresholds:
    """When an organ is considered stagnant (hours)."""
    posts: float = 24.0
    director_tasks: float = 12.0
    engagement: float = 48.0
    backups: float = 48.0


@dataclass
class OrganismConfig:
    workspace_id: str
    plan: str = "free"  # free | pro | business
    timezone: str = "UTC"
    industry_organs: List[str] = field(default_factory=lambda: ["default"])
    enabled_personas: Optional[List[str]] = None
    pulse: PulseThresholds = field(default_factory=PulseThresholds)
    director_authority: str = "suggest"  # suggest | notify | auto_low | auto_medium
    dream_hours_local: tuple = (1, 5)  # inclusive local hours for dreaming
    max_posts_per_day: int = 10
    metadata: Dict[str, Any] = field(default_factory=dict)

    def resolve_personas(self) -> List[str]:
        if self.enabled_personas:
            return list(self.enabled_personas)
        names: Set[str] = set(CORE_PERSONAS)  # canonical Council has 12 members
        for organ in self.industry_organs or ["default"]:
            for p in INDUSTRY_PERSONAS.get(organ, INDUSTRY_PERSONAS["default"]):
                names.add(p)
        return sorted(names)


class Organism:
    """
    One workspace's living company.

    Lazy-builds Council/Memory/Dreams so birth is cheap.
    """

    VERSION = "1.0.0"

    def __init__(
        self,
        config: OrganismConfig,
        *,
        container=None,
        event_bus=None,
    ) -> None:
        self.config = config
        self.workspace_id = config.workspace_id
        self._container = container
        self._event_bus = event_bus

        self.born_at = datetime.now(timezone.utc)
        self.last_active_at = self.born_at
        self._running = False
        # Serialize birth/death so concurrent authenticated requests cannot
        # publish duplicate lifecycle transitions for the same workspace.
        self._lifecycle_lock = asyncio.Lock()

        # Organs (lazy)
        self._council = None
        self._memory = None
        self._dreams = None
        self._director = None
        self._runtime = None
        self._decision_ids: Dict[str, Any] = {}

        self.stats: Dict[str, Any] = {
            "proposals": 0,
            "decisions": 0,
            "publishes": 0,
            "dreams": 0,
            "reflex_blocks": 0,
        }

    # ── Birth / death ─────────────────────────────────────────────────────

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return

            # The cognitive runtime is the workspace's canonical composition
            # boundary. Do not mark the organism alive if it cannot be built
            # or if its tenant identity does not match this organism.
            runtime = self.runtime
            if runtime is None:
                raise RuntimeError(
                    f"OrganismRuntime unavailable for workspace={self.workspace_id}"
                )
            if runtime.workspace_id != self.workspace_id:
                raise RuntimeError("OrganismRuntime workspace identity mismatch")

            # Recovery must complete before the organism is marked alive. The
            # backend service-role client is used only inside this server process;
            # browser clients and tenant-supplied identifiers never select a DB.
            if runtime.recovery_status == "not_attempted":
                try:
                    from services.supabase import supabase_service
                    service_client = supabase_service.service_client
                except Exception:
                    service_client = None
                if service_client is None:
                    runtime.recovery_status = "disabled_no_service_client"
                    logger.warning(
                        "Organism runtime persistence disabled workspace=%s",
                        self.workspace_id,
                    )
                else:
                    from organism.persistence import OrganismRuntimeStore
                    store = OrganismRuntimeStore(service_client)
                    await store.restore(runtime)
                    runtime.persistence_store = store

            # Materialize the workspace organs before publishing the birth
            # transition. Runtime construction remains lazy for dormant tenants.
            self.council
            self.memory
            self._running = True
            logger.info(
                "Organism born workspace=%s plan=%s personas=%s",
                self.workspace_id,
                self.config.plan,
                self.config.resolve_personas(),
            )
            if self._event_bus:
                try:
                    await self._event_bus.publish(
                        "organism.born",
                        {
                            "workspace_id": self.workspace_id,
                            "plan": self.config.plan,
                            "personas": self.config.resolve_personas(),
                            "industry_organs": self.config.industry_organs,
                        },
                        source="organism",
                        workspace_id=self.workspace_id,
                    )
                except Exception as exc:
                    # Birth telemetry must not silently disappear. The organism
                    # remains alive, while the durable bus reports the failure.
                    logger.warning(
                        "Organism birth event publish failed workspace=%s: %s",
                        self.workspace_id,
                        exc,
                    )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running:
                return
            if self._dreams and hasattr(self._dreams, "stop"):
                try:
                    await self._dreams.stop()
                except Exception as exc:
                    logger.warning(
                        "DreamSystem stop failed workspace=%s: %s",
                        self.workspace_id,
                        exc,
                    )
            # Keep the deterministic runtime instance and its in-process state
            # attached. A restart must not silently create a second mind.
            self._running = False
            logger.info("Organism stopped workspace=%s", self.workspace_id)

    def touch(self) -> None:
        self.last_active_at = datetime.now(timezone.utc)

    # ── Organs ────────────────────────────────────────────────────────────

    @property
    def council(self):
        if self._council is None:
            self._council = self._build_council()
        return self._council

    @property
    def memory(self):
        if self._memory is None:
            try:
                from core.memory import MemorySystem
                supabase = None
                if self._container:
                    try:
                        from services.supabase import supabase_service
                        supabase = getattr(supabase_service, "client", None)
                    except Exception:
                        pass
                self._memory = MemorySystem(supabase_client=supabase)
            except Exception as exc:
                logger.debug("MemorySystem unavailable: %s", exc)
                self._memory = _NullMemory()
        return self._memory

    @property
    def dreams(self):
        if self._dreams is None:
            try:
                from core.dreams import DreamSystem
                self._dreams = DreamSystem(
                    container=self._container,
                    council=self.council,
                )
                # Tag with workspace for multi-organism dreams
                self._dreams.workspace_id = self.workspace_id  # type: ignore[attr-defined]
                self._dreams.dream_hours = self.config.dream_hours_local  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("DreamSystem unavailable: %s", exc)
                self._dreams = None
        return self._dreams

    @property
    def runtime(self):
        """Unified Phase 37 organism composition boundary for this workspace."""
        if self._runtime is None:
            try:
                from organism.lifecycle import OrganismRuntime
                self._runtime = OrganismRuntime(self.workspace_id)
            except Exception as exc:
                logger.debug("OrganismRuntime unavailable: %s", exc)
                self._runtime = None
        return self._runtime

    @property
    def director(self):
        return self._director

    def bind_director(self, director) -> None:
        """Attach a Director instance (species may share code, state is per-ws)."""
        self._director = director

    def _build_council(self):
        try:
            from council.engine import DemocracyEngine
            from council.config import DemocracyConfig

            cfg = DemocracyConfig()
            # Plan-based metabolism for council depth
            if self.config.plan == "free":
                cfg.tier_2_persona_count = 3
                cfg.tier_3_persona_count = 5
                cfg.fail_open_on_low_risk = True
            elif self.config.plan == "pro":
                cfg.tier_2_persona_count = 5
                cfg.tier_3_persona_count = 8
            else:  # business
                cfg.tier_2_persona_count = 5
                cfg.tier_3_persona_count = 12

            # High risk always fail-closed at species law
            cfg.fail_closed_on_high_risk = True

            engine = DemocracyEngine(config=cfg)
            # The root Council is the canonical 12-member governance body.
            # Workspace persona settings describe contextual participation;
            # they must not mutate/remove members from the authoritative
            # Council itself. DecisionRouter/tier policy controls who votes.
            logger.info(
                "Organism %s: canonical Council=%s members=%d configured_personas=%s",
                self.workspace_id,
                engine.__class__.__module__,
                len(getattr(engine, "_personas", {}) or {}),
                self.config.resolve_personas(),
            )
            return engine
        except Exception as exc:
            logger.warning(
                "Organism %s: Council build failed: %s",
                self.workspace_id,
                exc,
            )
            return None

    # ── Metabolism / plan limits ──────────────────────────────────────────

    def director_may_auto(self, risk_level: str) -> bool:
        risk = (risk_level or "medium").lower()
        auth = self.config.director_authority
        if auth == "suggest":
            return False
        if auth == "notify":
            return False
        if auth == "auto_low":
            return risk in ("trivial", "low")
        if auth == "auto_medium":
            return risk in ("trivial", "low", "medium")
        return False

    def vitals(self) -> Dict[str, Any]:
        # Lifecycle truth is deliberately explicit.  ``running`` is the
        # process-local organism runtime state; it is not a claim that every
        # dependency or external provider is healthy.
        life_status = "ALIVE" if self._running else "STOPPED"
        # Compatibility aliases for control-plane evidence parsers that expect
        # status/health rather than life_status (KDO applyOrganismEvidence).
        status = "healthy" if self._running else "offline"
        return {
            "workspace_id": self.workspace_id,
            "plan": self.config.plan,
            "running": self._running,
            "life_status": life_status,
            "life_status_basis": "organism_runtime_started" if self._running else "organism_runtime_stopped",
            "status": status,
            "health": status,
            "born_at": self.born_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "personas": self.config.resolve_personas(),
            "industry_organs": self.config.industry_organs,
            "director_authority": self.config.director_authority,
            "pulse_thresholds": {
                "posts_hours": self.config.pulse.posts,
                "director_tasks_hours": self.config.pulse.director_tasks,
            },
            "stats": dict(self.stats),
            "has_council": self._council is not None,
            "has_memory": self._memory is not None,
            "has_dreams": self._dreams is not None,
            "has_director": self._director is not None,
        }


class _NullMemory:
    def get_recent_messages(self, *a, **k):
        return []


class OrganismRegistry:
    """
    Species census of living workspaces.

    Thread-safe get_or_create. Does not start dream loops automatically
    for every tenant (cost); call organism.start() on first authenticated use.
    """

    def __init__(self, container=None, event_bus=None) -> None:
        self._container = container
        self._event_bus = event_bus
        self._organisms: Dict[str, Organism] = {}
        self._lock = threading.RLock()

    def set_event_bus(self, bus) -> None:
        self._event_bus = bus

    def set_container(self, container) -> None:
        self._container = container

    def get(self, workspace_id: str) -> Optional[Organism]:
        with self._lock:
            return self._organisms.get(workspace_id)

    def get_or_create(
        self,
        workspace_id: str,
        *,
        plan: str = "free",
        industry_organs: Optional[List[str]] = None,
        timezone: str = "UTC",
        director_authority: Optional[str] = None,
        **kwargs,
    ) -> Organism:
        if not workspace_id:
            workspace_id = "default"
        with self._lock:
            existing = self._organisms.get(workspace_id)
            if existing:
                existing.touch()
                return existing

            # Prefer DB workspace_config when available
            try:
                from core.runtime_config import runtime_config
                wc = runtime_config.get_workspace(workspace_id)
                plan = plan or wc.plan
                timezone = timezone or wc.timezone
                industry_organs = industry_organs or list(wc.industry_organs)
                director_authority = director_authority or wc.director_authority
                enabled_personas = kwargs.pop("enabled_personas", None) or list(wc.enabled_personas)
                dream_hours = kwargs.pop("dream_hours_local", None) or wc.dream_hours
                pulse_posts = wc.pulse_posts_hours
            except Exception:
                enabled_personas = kwargs.pop("enabled_personas", None)
                dream_hours = kwargs.pop("dream_hours_local", None) or (1, 5)
                pulse_posts = 24.0

            auth = director_authority or _default_authority_for_plan(plan)
            config = OrganismConfig(
                workspace_id=workspace_id,
                plan=plan,
                timezone=timezone,
                industry_organs=industry_organs or ["default"],
                director_authority=auth,
                enabled_personas=enabled_personas,
                dream_hours_local=tuple(dream_hours) if dream_hours else (1, 5),
                max_posts_per_day=_max_posts_for_plan(plan),
                pulse=PulseThresholds(posts=float(pulse_posts)),
                metadata=kwargs,
            )
            org = Organism(
                config,
                container=self._container,
                event_bus=self._event_bus,
            )
            self._organisms[workspace_id] = org
            logger.info(
                "OrganismRegistry: created workspace=%s plan=%s (census=%d)",
                workspace_id,
                plan,
                len(self._organisms),
            )
            try:
                from core.species_scale import organism_state_store
                organism_state_store.save(workspace_id, org.vitals())
            except Exception:
                pass
            return org

    async def ensure_started(self, workspace_id: str, **kwargs) -> Organism:
        org = self.get_or_create(workspace_id, **kwargs)
        if not org._running:
            await org.start()
        return org

    def list_ids(self) -> List[str]:
        with self._lock:
            return list(self._organisms.keys())

    def census(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [o.vitals() for o in self._organisms.values()]

    async def stop_all(self) -> None:
        with self._lock:
            orgs = list(self._organisms.values())
        for org in orgs:
            await org.stop()

    def remove(self, workspace_id: str) -> None:
        with self._lock:
            org = self._organisms.pop(workspace_id, None)
        if org:
            asyncio.get_event_loop().create_task(org.stop()) if _loop_running() else None


def _default_authority_for_plan(plan: str) -> str:
    try:
        from core.metabolism import get_plan_limits
        return get_plan_limits(plan).director_authority
    except Exception:
        return {
            "free": "suggest",
            "pro": "auto_low",
            "business": "auto_medium",
            "enterprise": "auto_medium",
        }.get(plan, "suggest")


def _max_posts_for_plan(plan: str) -> int:
    try:
        from core.metabolism import get_plan_limits
        return get_plan_limits(plan).max_posts_per_day
    except Exception:
        return {"free": 5, "pro": 20, "business": 100, "enterprise": 1000}.get(plan, 5)


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


# Process-global registry (species)
organism_registry = OrganismRegistry()


def get_organism(workspace_id: str, **kwargs) -> Organism:
    """Convenience: get or create without starting."""
    return organism_registry.get_or_create(workspace_id, **kwargs)
