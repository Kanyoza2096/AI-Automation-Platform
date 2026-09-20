"""
Phase F — Species scale.

1. SERVICE_MODE roles are explicit and documented for multi-process deploy.
2. Chaos Monkey cannot start in production (fail-closed).
3. Organism state is durable: census + plan/config hydrate from Supabase
   so a process restart does not amnesia the herd.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.core.species_scale")


# ── Service roles ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServiceRole:
    name: str
    description: str
    boots: tuple  # high-level capability tags


SERVICE_ROLES: Dict[str, ServiceRole] = {
    "full": ServiceRole(
        "full",
        "Monolith — all organs in one process (dev / small deploy)",
        ("api", "brain", "workers", "monitor"),
    ),
    "api": ServiceRole(
        "api",
        "HTTP edge — auth, organism body, metabolism, industry plugins",
        ("api",),
    ),
    "core": ServiceRole(
        "core",
        "Brain process — Director, Meta, Condition, AutonomousLoop, synapses",
        ("brain",),
    ),
    "director": ServiceRole("director", "Director-only slice", ("brain",)),
    "meta": ServiceRole("meta", "MetaEngine-only slice", ("brain",)),
    "condition": ServiceRole("condition", "ConditionEngine-only slice", ("brain",)),
    "workers": ServiceRole(
        "workers",
        "Schedulers and job runners (hands)",
        ("workers",),
    ),
    "monitor": ServiceRole(
        "monitor",
        "Pulse / backup / trend observers",
        ("monitor",),
    ),
    "github": ServiceRole("github", "GitHub connector worker", ("monitor",)),
    "chaos": ServiceRole(
        "chaos",
        "Full stack + Chaos Monkey — NEVER production",
        ("api", "brain", "workers", "monitor", "chaos"),
    ),
}


def resolve_service_mode() -> str:
    mode = (os.getenv("SERVICE_MODE") or "").strip().lower()
    if not mode:
        try:
            from config.settings import settings
            mode = (getattr(settings, "service_mode", None) or "full").lower()
        except Exception:
            mode = "full"
    if mode not in SERVICE_ROLES:
        logger.warning("Unknown SERVICE_MODE=%s — falling back to full", mode)
        return "full"
    return mode


def role_info(mode: Optional[str] = None) -> Dict[str, Any]:
    m = mode or resolve_service_mode()
    role = SERVICE_ROLES.get(m, SERVICE_ROLES["full"])
    return {
        "mode": role.name,
        "description": role.description,
        "capabilities": list(role.boots),
    }


# ── Chaos safety ──────────────────────────────────────────────────────────

class ChaosForbiddenError(RuntimeError):
    """Raised when chaos is requested in a forbidden environment."""


def environment_name() -> str:
    try:
        from config.settings import settings
        return (getattr(settings, "environment", None) or "production").lower()
    except Exception:
        return (os.getenv("ENVIRONMENT") or "production").lower()


def is_production() -> bool:
    return environment_name() in ("production", "prod")


def assert_chaos_allowed() -> None:
    """
    Phase F law: Chaos Monkey only in non-production.

    Even SERVICE_MODE=chaos is refused when ENVIRONMENT=production.
    Override only with CHAOS_ALLOW_PRODUCTION=1 (emergency drills — not recommended).
    """
    if os.getenv("CHAOS_ALLOW_PRODUCTION", "").strip() in ("1", "true", "yes"):
        logger.critical("CHAOS_ALLOW_PRODUCTION override active — chaos allowed in production")
        return
    if is_production():
        raise ChaosForbiddenError(
            "Chaos Monkey is forbidden in production. "
            "Set ENVIRONMENT=staging|development and SERVICE_MODE=chaos."
        )
    env = environment_name()
    if env not in ("staging", "development", "dev", "test", "testing", "local"):
        raise ChaosForbiddenError(
            f"Chaos Monkey refused for ENVIRONMENT={env}. "
            "Allowed: staging, development, test, local."
        )


def gate_chaos_monkey(monkey) -> bool:
    """
    Wrap ChaosMonkey.start. Returns True if started, False if blocked.
    """
    try:
        assert_chaos_allowed()
    except ChaosForbiddenError as exc:
        logger.error("Chaos blocked: %s", exc)
        return False
    monkey.start()
    return True


# ── Durable organism state ────────────────────────────────────────────────

class OrganismStateStore:
    """
    Persist/hydrate OrganismRegistry census.

    Table: organism_state (workspace_id PK)
    """

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client

    def set_supabase(self, client) -> None:
        self._db = client

    def save(self, workspace_id: str, payload: Dict[str, Any]) -> None:
        if not self._db:
            return
        row = {
            "workspace_id": workspace_id,
            "plan": payload.get("plan", "free"),
            "director_authority": payload.get("director_authority"),
            "industry_organs": payload.get("industry_organs") or [],
            "timezone": payload.get("timezone") or "UTC",
            "stats": payload.get("stats") or {},
            "vitals": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._db.table("organism_state").upsert(
                row, on_conflict="workspace_id"
            ).execute()
        except Exception as exc:
            logger.debug("organism_state save failed: %s", exc)

    def load(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        if not self._db:
            return None
        try:
            res = (
                self._db.table("organism_state")
                .select("*")
                .eq("workspace_id", workspace_id)
                .maybe_single()
                .execute()
            )
            return res.data if res and res.data else None
        except Exception as exc:
            logger.debug("organism_state load failed: %s", exc)
            return None

    def load_all(self) -> List[Dict[str, Any]]:
        if not self._db:
            return []
        try:
            res = self._db.table("organism_state").select("*").execute()
            return list(res.data or [])
        except Exception as exc:
            logger.debug("organism_state load_all failed: %s", exc)
            return []

    def persist_registry(self, registry) -> int:
        """Write current census to durable store. Returns count saved."""
        n = 0
        for vitals in registry.census():
            self.save(vitals["workspace_id"], vitals)
            n += 1
        return n

    def hydrate_registry(self, registry) -> int:
        """Rebirth organisms from durable store. Returns count hydrated."""
        rows = self.load_all()
        n = 0
        for row in rows:
            ws = row.get("workspace_id")
            if not ws:
                continue
            org = registry.get_or_create(
                ws,
                plan=row.get("plan") or "free",
                industry_organs=row.get("industry_organs") or ["default"],
                timezone=row.get("timezone") or "UTC",
                director_authority=row.get("director_authority"),
            )
            # restore stats if present
            if row.get("stats") and isinstance(row["stats"], dict):
                org.stats.update(row["stats"])
            n += 1
        if n:
            logger.info("Hydrated %d organisms from durable state", n)
        return n


organism_state_store = OrganismStateStore()


async def boot_species_scale(container, event_bus=None) -> Dict[str, Any]:
    """
    Lifecycle hook: hydrate census, register role, refuse chaos in prod.
    """
    mode = resolve_service_mode()
    info = role_info(mode)
    logger.info("Species scale role: %s — %s", info["mode"], info["description"])

    # Chaos hard gate
    if mode == "chaos" or os.getenv("CHAOS_ENABLED", "").lower() in ("1", "true"):
        try:
            assert_chaos_allowed()
            logger.warning("Chaos mode permitted in ENVIRONMENT=%s", environment_name())
        except ChaosForbiddenError as exc:
            logger.error("%s — forcing SERVICE_MODE away from chaos at runtime note", exc)
            info["chaos_blocked"] = True
            info["chaos_error"] = str(exc)

    # Durable hydrate
    try:
        from services.supabase import supabase_service
        if supabase_service and supabase_service.client:
            organism_state_store.set_supabase(supabase_service.client)
    except Exception:
        pass

    hydrated = 0
    try:
        from core.organism import organism_registry
        organism_registry.set_container(container)
        if event_bus:
            organism_registry.set_event_bus(event_bus)
        hydrated = organism_state_store.hydrate_registry(organism_registry)
    except Exception as exc:
        logger.warning("Organism hydrate failed: %s", exc)

    return {
        **info,
        "organisms_hydrated": hydrated,
        "environment": environment_name(),
        "is_production": is_production(),
    }


def persist_all_organisms() -> int:
    """Call on shutdown or periodic flush."""
    try:
        from core.organism import organism_registry
        return organism_state_store.persist_registry(organism_registry)
    except Exception as exc:
        logger.debug("persist_all_organisms: %s", exc)
        return 0
