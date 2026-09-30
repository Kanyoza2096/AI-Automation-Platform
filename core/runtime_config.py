"""
DB-backed runtime configuration with safe in-code defaults.

Loads:
  - plans (metabolism limits)
  - workspace_config (personas, pulse, dream hours, capabilities)
  - brand_config (keywords, fallback topics)
  - persona_catalog

Falls back to hardcoded defaults when DB is empty/unavailable so the
organism still boots offline.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("kanyoza.core.runtime_config")

# ── Hardcoded defaults (used until DB overrides) ─────────────────────────

DEFAULT_PLANS: Dict[str, Dict[str, Any]] = {
    "free": {
        "max_posts_per_day": 5,
        "max_ai_tokens_per_month": 50_000,
        "max_brands": 1,
        "max_social_accounts": 2,
        "director_authority": "suggest",
        "industry_organs": ["default"],
        "council_tier_3_personas": 5,
        "price_usd_monthly": 0,
    },
    "pro": {
        "max_posts_per_day": 20,
        "max_ai_tokens_per_month": 500_000,
        "max_brands": 5,
        "max_social_accounts": 10,
        "director_authority": "auto_low",
        "industry_organs": ["default", "crm", "church_mis"],
        "council_tier_3_personas": 8,
        "price_usd_monthly": 49,
    },
    "business": {
        "max_posts_per_day": 100,
        "max_ai_tokens_per_month": 2_000_000,
        "max_brands": 25,
        "max_social_accounts": 50,
        "director_authority": "auto_medium",
        "industry_organs": ["default", "crm", "church_mis", "hospital", "school_mis"],
        "council_tier_3_personas": 12,
        "price_usd_monthly": 149,
    },
    "enterprise": {
        "max_posts_per_day": 1000,
        "max_ai_tokens_per_month": 20_000_000,
        "max_brands": 100,
        "max_social_accounts": 200,
        "director_authority": "auto_medium",
        "industry_organs": ["default", "crm", "church_mis", "hospital", "school_mis"],
        "council_tier_3_personas": 12,
        "price_usd_monthly": 0,
    },
}

DEFAULT_PERSONAS = [
    "youth_advocate", "business_owner", "community_elder", "data_analyst",
    "risk_manager", "trend_spotter", "customer_voice", "gut_feeling",
    "mood_ring", "rippler",
]

DEFAULT_CAPABILITIES = [
    "publish_scheduled_content", "optimize_content_schedule", "calculate_brand_health",
    "queue_brand_revival", "run_health_scan", "restart_failed_service",
    "run_guardian_scan", "quarantine_security_issue", "prepare_security_remediation",
    "generate_client_report", "prepare_client_message", "analyze_growth_opportunity",
    "optimize_infrastructure", "run_learning_cycle", "self_diagnostic",
]


@dataclass
class WorkspaceRuntimeConfig:
    workspace_id: str
    plan: str = "free"
    timezone: str = "UTC"
    dream_hours: Tuple[int, int] = (1, 5)
    enabled_personas: List[str] = field(default_factory=lambda: list(DEFAULT_PERSONAS))
    industry_organs: List[str] = field(default_factory=lambda: ["default"])
    director_authority: str = "suggest"
    enabled_capabilities: List[str] = field(default_factory=lambda: list(DEFAULT_CAPABILITIES))
    pulse_posts_hours: float = 24.0
    pulse_director_hours: float = 12.0
    pulse_engagement_hours: float = 48.0
    pulse_backup_hours: float = 48.0
    source: str = "defaults"  # defaults | db | merged


@dataclass
class BrandRuntimeConfig:
    brand_id: str
    workspace_id: str = "default"
    keywords: List[str] = field(default_factory=list)
    fallback_topics: List[str] = field(default_factory=list)
    content_categories: List[str] = field(default_factory=list)
    tone: Optional[str] = None
    language: str = "en"
    source: str = "defaults"


class RuntimeConfigStore:
    """Thread-safe config cache with optional Supabase backend."""

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client
        self._lock = threading.RLock()
        self._plans: Dict[str, Dict[str, Any]] = dict(DEFAULT_PLANS)
        self._workspace: Dict[str, WorkspaceRuntimeConfig] = {}
        self._brands: Dict[str, BrandRuntimeConfig] = {}
        self._personas: List[Dict[str, Any]] = []
        self._loaded_at: Optional[datetime] = None

    def set_supabase(self, client) -> None:
        self._db = client

    def refresh(self) -> None:
        """Reload plans + persona catalog from DB (workspace/brand stay lazy)."""
        with self._lock:
            self._load_plans()
            self._load_personas()
            self._loaded_at = datetime.now(timezone.utc)

    def _load_plans(self) -> None:
        if not self._db:
            self._plans = dict(DEFAULT_PLANS)
            return
        try:
            res = self._db.table("plans").select("*").eq("enabled", True).execute()
            if res.data:
                plans = {}
                for row in res.data:
                    plans[row["name"]] = {
                        "max_posts_per_day": row.get("max_posts_per_day", 5),
                        "max_ai_tokens_per_month": row.get("max_ai_tokens_per_month", 50_000),
                        "max_brands": row.get("max_brands", 1),
                        "max_social_accounts": row.get("max_social_accounts", 2),
                        "director_authority": row.get("director_authority") or "suggest",
                        "industry_organs": row.get("industry_organs") or ["default"],
                        "council_tier_3_personas": row.get("council_tier_3_personas") or 5,
                        "price_usd_monthly": row.get("price_usd_monthly") or 0,
                    }
                self._plans = plans
                logger.info("RuntimeConfig: loaded %d plans from DB", len(plans))
            else:
                self._plans = dict(DEFAULT_PLANS)
        except Exception as exc:
            logger.debug("plans load failed, using defaults: %s", exc)
            self._plans = dict(DEFAULT_PLANS)

    def _load_personas(self) -> None:
        if not self._db:
            self._personas = [{"name": n, "default_enabled": True} for n in DEFAULT_PERSONAS]
            return
        try:
            res = self._db.table("persona_catalog").select("*").execute()
            self._personas = list(res.data or [])
        except Exception as exc:
            logger.debug("persona_catalog load failed: %s", exc)
            self._personas = [{"name": n, "default_enabled": True} for n in DEFAULT_PERSONAS]

    def get_plan(self, name: str) -> Dict[str, Any]:
        with self._lock:
            if not self._plans:
                self._plans = dict(DEFAULT_PLANS)
            return dict(self._plans.get((name or "free").lower(), self._plans.get("free", DEFAULT_PLANS["free"])))

    def list_plans(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._plans.items()}

    def get_workspace(self, workspace_id: str) -> WorkspaceRuntimeConfig:
        workspace_id = workspace_id or "default"
        with self._lock:
            if workspace_id in self._workspace:
                return self._workspace[workspace_id]
        cfg = self._fetch_workspace(workspace_id)
        with self._lock:
            self._workspace[workspace_id] = cfg
        return cfg

    def _fetch_workspace(self, workspace_id: str) -> WorkspaceRuntimeConfig:
        plan_name = "free"
        if not self._db:
            plan = self.get_plan(plan_name)
            return WorkspaceRuntimeConfig(
                workspace_id=workspace_id,
                plan=plan_name,
                director_authority=plan["director_authority"],
                industry_organs=list(plan.get("industry_organs") or ["default"]),
                source="defaults",
            )
        try:
            res = (
                self._db.table("workspace_config")
                .select("*")
                .eq("workspace_id", workspace_id)
                .maybe_single()
                .execute()
            )
            row = res.data if res else None
        except Exception as exc:
            logger.debug("workspace_config fetch: %s", exc)
            row = None

        if not row:
            # try organism_state / workspaces.plan
            try:
                w = (
                    self._db.table("workspaces")
                    .select("plan")
                    .eq("id", workspace_id)
                    .maybe_single()
                    .execute()
                )
                if w and w.data and w.data.get("plan"):
                    plan_name = w.data["plan"]
            except Exception:
                pass
            plan = self.get_plan(plan_name)
            return WorkspaceRuntimeConfig(
                workspace_id=workspace_id,
                plan=plan_name,
                director_authority=plan["director_authority"],
                industry_organs=list(plan.get("industry_organs") or ["default"]),
                source="defaults",
            )

        plan_name = row.get("plan") or "free"
        plan = self.get_plan(plan_name)
        personas = row.get("enabled_personas")
        if not personas:
            personas = list(DEFAULT_PERSONAS)
            # add industry personas from catalog
            organs = row.get("industry_organs") or plan.get("industry_organs") or ["default"]
            for p in self._personas:
                inds = p.get("industries") or ["default"]
                if any(o in inds for o in organs) and p.get("name"):
                    if p["name"] not in personas:
                        personas.append(p["name"])

        caps = row.get("enabled_capabilities") or list(DEFAULT_CAPABILITIES)
        authority = row.get("director_authority") or plan["director_authority"]
        organs = row.get("industry_organs") or plan.get("industry_organs") or ["default"]
        return WorkspaceRuntimeConfig(
            workspace_id=workspace_id,
            plan=plan_name,
            timezone=row.get("timezone") or "UTC",
            dream_hours=(
                int(row.get("dream_hours_start") or 1),
                int(row.get("dream_hours_end") or 5),
            ),
            enabled_personas=list(personas),
            industry_organs=list(organs),
            director_authority=authority,
            enabled_capabilities=list(caps),
            pulse_posts_hours=float(row.get("pulse_posts_hours") or 24),
            pulse_director_hours=float(row.get("pulse_director_hours") or 12),
            pulse_engagement_hours=float(row.get("pulse_engagement_hours") or 48),
            pulse_backup_hours=float(row.get("pulse_backup_hours") or 48),
            source="db",
        )

    def get_brand(self, brand_id: str, workspace_id: str = "default") -> BrandRuntimeConfig:
        if not brand_id:
            return BrandRuntimeConfig(brand_id="default", workspace_id=workspace_id)
        with self._lock:
            if brand_id in self._brands:
                return self._brands[brand_id]
        cfg = self._fetch_brand(brand_id, workspace_id)
        with self._lock:
            self._brands[brand_id] = cfg
        return cfg

    def _fetch_brand(self, brand_id: str, workspace_id: str) -> BrandRuntimeConfig:
        if not self._db:
            return BrandRuntimeConfig(brand_id=brand_id, workspace_id=workspace_id, source="defaults")
        try:
            res = (
                self._db.table("brand_config")
                .select("*")
                .eq("brand_id", brand_id)
                .maybe_single()
                .execute()
            )
            row = res.data if res else None
        except Exception as exc:
            logger.debug("brand_config fetch: %s", exc)
            row = None
        if not row:
            return BrandRuntimeConfig(brand_id=brand_id, workspace_id=workspace_id, source="defaults")
        return BrandRuntimeConfig(
            brand_id=brand_id,
            workspace_id=row.get("workspace_id") or workspace_id,
            keywords=list(row.get("keywords") or []),
            fallback_topics=list(row.get("fallback_topics") or []),
            content_categories=list(row.get("content_categories") or []),
            tone=row.get("tone"),
            language=row.get("language") or "en",
            source="db",
        )

    def invalidate_workspace(self, workspace_id: str) -> None:
        with self._lock:
            self._workspace.pop(workspace_id, None)

    def invalidate_brand(self, brand_id: str) -> None:
        with self._lock:
            self._brands.pop(brand_id, None)


runtime_config = RuntimeConfigStore()
