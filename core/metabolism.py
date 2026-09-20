"""
Phase D — Metabolism (plans as biological limits).

Every organism has a metabolic budget:

  - posts / day
  - AI thought tokens / month
  - brands
  - social accounts
  - director auto-authority
  - industry organs unlocked

Usage is metered in-process (and optionally persisted). Enforcement
happens *before* publish and *before* expensive AI calls.

Stripe is optional: when STRIPE_SECRET_KEY is set, Checkout + webhook
upgrade the workspace plan; without Stripe, plan is admin-set.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("kanyoza.core.metabolism")


# ── Plan catalog (species product) ────────────────────────────────────────

@dataclass(frozen=True)
class PlanLimits:
    name: str
    max_posts_per_day: int
    max_ai_tokens_per_month: int
    max_brands: int
    max_social_accounts: int
    max_workspaces_hint: int  # soft; owner-level
    director_authority: str  # suggest | notify | auto_low | auto_medium
    industry_organs: Tuple[str, ...]
    council_tier_3_personas: int
    price_usd_monthly: int  # 0 = free
    stripe_price_id_env: str = ""  # env var name holding Stripe price id


PLAN_CATALOG: Dict[str, PlanLimits] = {
    "free": PlanLimits(
        name="free",
        max_posts_per_day=5,
        max_ai_tokens_per_month=50_000,
        max_brands=1,
        max_social_accounts=2,
        max_workspaces_hint=1,
        director_authority="suggest",
        industry_organs=("default",),
        council_tier_3_personas=5,
        price_usd_monthly=0,
    ),
    "pro": PlanLimits(
        name="pro",
        max_posts_per_day=20,
        max_ai_tokens_per_month=500_000,
        max_brands=5,
        max_social_accounts=10,
        max_workspaces_hint=3,
        director_authority="auto_low",
        industry_organs=("default", "crm", "church_mis"),
        council_tier_3_personas=8,
        price_usd_monthly=49,
        stripe_price_id_env="STRIPE_PRICE_PRO",
    ),
    "business": PlanLimits(
        name="business",
        max_posts_per_day=100,
        max_ai_tokens_per_month=2_000_000,
        max_brands=25,
        max_social_accounts=50,
        max_workspaces_hint=20,
        director_authority="auto_medium",
        industry_organs=("default", "crm", "church_mis", "hospital", "school_mis"),
        council_tier_3_personas=12,
        price_usd_monthly=149,
        stripe_price_id_env="STRIPE_PRICE_BUSINESS",
    ),
    "enterprise": PlanLimits(
        name="enterprise",
        max_posts_per_day=1000,
        max_ai_tokens_per_month=20_000_000,
        max_brands=100,
        max_social_accounts=200,
        max_workspaces_hint=999,
        director_authority="auto_medium",
        industry_organs=("default", "crm", "church_mis", "hospital", "school_mis"),
        council_tier_3_personas=12,
        price_usd_monthly=0,  # custom
        stripe_price_id_env="STRIPE_PRICE_ENTERPRISE",
    ),
}


def get_plan_limits(plan: str) -> PlanLimits:
    """Prefer DB-backed plans via runtime_config; fall back to in-code catalog."""
    name = (plan or "free").lower()
    try:
        from core.runtime_config import runtime_config
        row = runtime_config.get_plan(name)
        if row:
            return PlanLimits(
                name=name,
                max_posts_per_day=int(row.get("max_posts_per_day", 5)),
                max_ai_tokens_per_month=int(row.get("max_ai_tokens_per_month", 50_000)),
                max_brands=int(row.get("max_brands", 1)),
                max_social_accounts=int(row.get("max_social_accounts", 2)),
                max_workspaces_hint=1,
                director_authority=str(row.get("director_authority") or "suggest"),
                industry_organs=tuple(row.get("industry_organs") or ("default",)),
                council_tier_3_personas=int(row.get("council_tier_3_personas") or 5),
                price_usd_monthly=int(row.get("price_usd_monthly") or 0),
            )
    except Exception:
        pass
    return PLAN_CATALOG.get(name, PLAN_CATALOG["free"])


# ── Usage meter ───────────────────────────────────────────────────────────

@dataclass
class UsageSnapshot:
    workspace_id: str
    day: str  # YYYY-MM-DD
    month: str  # YYYY-MM
    posts_today: int = 0
    ai_tokens_month: int = 0
    brands: int = 0
    social_accounts: int = 0


class MetabolismError(PermissionError):
    """Raised when an organism is over its metabolic budget."""

    def __init__(self, code: str, message: str, *, usage: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.usage = usage or {}


class UsageMeter:
    """
    Thread-safe in-memory usage counters per workspace.

    Optionally mirrors to Supabase table `workspace_usage` when client is set.
    """

    def __init__(self, supabase_client=None) -> None:
        self._db = supabase_client
        self._lock = threading.RLock()
        # key: (workspace_id, day) → posts
        self._posts: Dict[Tuple[str, str], int] = {}
        # key: (workspace_id, month) → tokens
        self._tokens: Dict[Tuple[str, str], int] = {}
        self._brands: Dict[str, int] = {}
        self._social: Dict[str, int] = {}

    def set_supabase(self, client) -> None:
        self._db = client

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    @staticmethod
    def _month() -> str:
        return date.today().strftime("%Y-%m")

    def snapshot(self, workspace_id: str) -> UsageSnapshot:
        day, month = self._today(), self._month()
        with self._lock:
            return UsageSnapshot(
                workspace_id=workspace_id,
                day=day,
                month=month,
                posts_today=self._posts.get((workspace_id, day), 0),
                ai_tokens_month=self._tokens.get((workspace_id, month), 0),
                brands=self._brands.get(workspace_id, 0),
                social_accounts=self._social.get(workspace_id, 0),
            )

    def record_post(self, workspace_id: str, count: int = 1) -> int:
        """Record usage. Multi-instance deployments use an atomic DB RPC."""
        count = max(0, int(count))
        if self._db is not None and count == 1:
            try:
                # This method is legacy-compatible; quota enforcement happens in Metabolism.
                res = self._db.rpc("increment_workspace_post_usage", {"p_workspace_id": workspace_id, "p_count": count}).execute()
                if res.data:
                    row = res.data[0] if isinstance(res.data, list) else res.data
                    return int(row.get("posts_today", 0))
            except Exception as exc:
                if self._is_production():
                    raise RuntimeError("Distributed usage persistence unavailable") from exc
        day = self._today()
        with self._lock:
            key = (workspace_id, day)
            self._posts[key] = self._posts.get(key, 0) + count
            total = self._posts[key]
        self._persist(workspace_id)
        return total

    def record_ai_tokens(self, workspace_id: str, tokens: int) -> int:
        tokens = max(0, int(tokens))
        if self._db is not None:
            try:
                res = self._db.rpc("increment_workspace_ai_usage", {"p_workspace_id": workspace_id, "p_tokens": tokens}).execute()
                if res.data:
                    row = res.data[0] if isinstance(res.data, list) else res.data
                    return int(row.get("ai_tokens_month", 0))
            except Exception as exc:
                if self._is_production():
                    raise RuntimeError("Distributed usage persistence unavailable") from exc
        month = self._month()
        with self._lock:
            key = (workspace_id, month)
            self._tokens[key] = self._tokens.get(key, 0) + tokens
            total = self._tokens[key]
        self._persist(workspace_id)
        return total

    @staticmethod
    def _is_production() -> bool:
        import os
        return (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development").lower() in ("production", "prod", "staging")

    def set_resource_counts(
        self, workspace_id: str, *, brands: Optional[int] = None, social_accounts: Optional[int] = None
    ) -> None:
        with self._lock:
            if brands is not None:
                self._brands[workspace_id] = brands
            if social_accounts is not None:
                self._social[workspace_id] = social_accounts

    def _persist(self, workspace_id: str) -> None:
        if not self._db:
            return
        snap = self.snapshot(workspace_id)
        try:
            self._db.table("workspace_usage").upsert(
                {
                    "workspace_id": workspace_id,
                    "day": snap.day,
                    "month": snap.month,
                    "posts_today": snap.posts_today,
                    "ai_tokens_month": snap.ai_tokens_month,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="workspace_id,day",
            ).execute()
        except Exception as exc:
            logger.debug("usage persist failed: %s", exc)


usage_meter = UsageMeter()


# ── Enforcement ───────────────────────────────────────────────────────────

class Metabolism:
    """Check and consume budget for a workspace."""

    def __init__(self, meter: Optional[UsageMeter] = None) -> None:
        self.meter = meter or usage_meter

    def limits_for(self, workspace_id: str, plan: Optional[str] = None) -> PlanLimits:
        if plan is None:
            plan = self._plan_from_organism(workspace_id)
        return get_plan_limits(plan)

    def _plan_from_organism(self, workspace_id: str) -> str:
        try:
            from core.organism import organism_registry
            org = organism_registry.get(workspace_id)
            if org:
                return org.config.plan
        except Exception:
            pass
        return "free"

    def status(self, workspace_id: str, plan: Optional[str] = None) -> Dict[str, Any]:
        limits = self.limits_for(workspace_id, plan)
        snap = self.meter.snapshot(workspace_id)
        return {
            "workspace_id": workspace_id,
            "plan": limits.name,
            "limits": {
                "max_posts_per_day": limits.max_posts_per_day,
                "max_ai_tokens_per_month": limits.max_ai_tokens_per_month,
                "max_brands": limits.max_brands,
                "max_social_accounts": limits.max_social_accounts,
                "director_authority": limits.director_authority,
                "industry_organs": list(limits.industry_organs),
                "price_usd_monthly": limits.price_usd_monthly,
            },
            "usage": {
                "posts_today": snap.posts_today,
                "ai_tokens_month": snap.ai_tokens_month,
                "brands": snap.brands,
                "social_accounts": snap.social_accounts,
                "day": snap.day,
                "month": snap.month,
            },
            "remaining": {
                "posts_today": max(0, limits.max_posts_per_day - snap.posts_today),
                "ai_tokens_month": max(0, limits.max_ai_tokens_per_month - snap.ai_tokens_month),
            },
        }

    def assert_can_publish(self, workspace_id: str, plan: Optional[str] = None) -> None:
        limits = self.limits_for(workspace_id, plan)
        snap = self.meter.snapshot(workspace_id)
        if snap.posts_today >= limits.max_posts_per_day:
            raise MetabolismError(
                "posts_quota_exceeded",
                f"Daily post limit reached ({limits.max_posts_per_day}) for plan '{limits.name}'. "
                "Upgrade metabolism or wait until tomorrow.",
                usage=self.status(workspace_id, plan),
            )

    def assert_can_think(self, workspace_id: str, estimated_tokens: int = 0, plan: Optional[str] = None) -> None:
        limits = self.limits_for(workspace_id, plan)
        snap = self.meter.snapshot(workspace_id)
        if snap.ai_tokens_month + max(0, estimated_tokens) > limits.max_ai_tokens_per_month:
            raise MetabolismError(
                "ai_quota_exceeded",
                f"Monthly AI thought budget reached ({limits.max_ai_tokens_per_month}) for plan '{limits.name}'.",
                usage=self.status(workspace_id, plan),
            )

    def assert_can_add_brand(self, workspace_id: str, current_count: Optional[int] = None, plan: Optional[str] = None) -> None:
        limits = self.limits_for(workspace_id, plan)
        count = current_count if current_count is not None else self.meter.snapshot(workspace_id).brands
        if count >= limits.max_brands:
            raise MetabolismError(
                "brands_quota_exceeded",
                f"Brand limit ({limits.max_brands}) reached for plan '{limits.name}'.",
                usage=self.status(workspace_id, plan),
            )

    def assert_organ_allowed(self, workspace_id: str, organ: str, plan: Optional[str] = None) -> None:
        limits = self.limits_for(workspace_id, plan)
        if organ not in limits.industry_organs and organ != "default":
            raise MetabolismError(
                "organ_locked",
                f"Industry organ '{organ}' is not unlocked on plan '{limits.name}'.",
                usage=self.status(workspace_id, plan),
            )

    def consume_post(self, workspace_id: str, plan: Optional[str] = None) -> Dict[str, Any]:
        limits = self.limits_for(workspace_id, plan)
        if self.meter._db is not None:
            try:
                res = self.meter._db.rpc("consume_workspace_post_quota", {
                    "p_workspace_id": workspace_id, "p_limit": limits.max_posts_per_day
                }).execute()
                row = res.data[0] if res.data else None
                if not row or not row.get("allowed"):
                    raise MetabolismError("posts_quota_exceeded", f"Daily post limit reached ({limits.max_posts_per_day}) for plan '{limits.name}'.", usage=self.status(workspace_id, plan))
                return self.status(workspace_id, plan)
            except MetabolismError:
                raise
            except Exception as exc:
                if self.meter._is_production():
                    raise MetabolismError("usage_store_unavailable", "Distributed usage store unavailable; publish denied.", usage=self.status(workspace_id, plan)) from exc
        self.assert_can_publish(workspace_id, plan)
        self.meter.record_post(workspace_id)
        return self.status(workspace_id, plan)

    def consume_ai(self, workspace_id: str, tokens: int, plan: Optional[str] = None) -> Dict[str, Any]:
        limits = self.limits_for(workspace_id, plan)
        tokens = max(0, int(tokens))
        if self.meter._db is not None:
            try:
                res = self.meter._db.rpc("consume_workspace_ai_quota", {
                    "p_workspace_id": workspace_id, "p_tokens": tokens, "p_limit": limits.max_ai_tokens_per_month
                }).execute()
                row = res.data[0] if res.data else None
                if not row or not row.get("allowed"):
                    raise MetabolismError("ai_quota_exceeded", f"Monthly AI thought budget reached ({limits.max_ai_tokens_per_month}) for plan '{limits.name}'.", usage=self.status(workspace_id, plan))
                return self.status(workspace_id, plan)
            except MetabolismError:
                raise
            except Exception as exc:
                if self.meter._is_production():
                    raise MetabolismError("usage_store_unavailable", "Distributed usage store unavailable; AI request denied.", usage=self.status(workspace_id, plan)) from exc
        self.assert_can_think(workspace_id, tokens, plan)
        self.meter.record_ai_tokens(workspace_id, tokens)
        return self.status(workspace_id, plan)

    def apply_plan_to_organism(self, workspace_id: str, plan: str) -> Dict[str, Any]:
        """Upgrade/downgrade living organism to match plan limits."""
        limits = get_plan_limits(plan)
        try:
            from core.organism import organism_registry, OrganismConfig
            org = organism_registry.get(workspace_id)
            if org:
                org.config.plan = limits.name
                org.config.director_authority = limits.director_authority
                org.config.max_posts_per_day = limits.max_posts_per_day
                org.config.industry_organs = list(limits.industry_organs)
                # Force rebuild council with new tier sizes on next access
                org._council = None
            else:
                organism_registry.get_or_create(
                    workspace_id,
                    plan=limits.name,
                    industry_organs=list(limits.industry_organs),
                    director_authority=limits.director_authority,
                )
        except Exception as exc:
            logger.warning("apply_plan_to_organism failed: %s", exc)
        return self.status(workspace_id, plan)


metabolism = Metabolism()


# ── Stripe adapter (optional) ─────────────────────────────────────────────

class StripeBilling:
    """Thin Stripe wrapper. No-op if STRIPE_SECRET_KEY missing."""

    def __init__(self) -> None:
        self._key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
        self._webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        self._stripe = None
        if self._key:
            try:
                import stripe
                stripe.api_key = self._key
                self._stripe = stripe
                logger.info("StripeBilling enabled")
            except ImportError:
                logger.warning("stripe package not installed — billing API disabled")

    @property
    def enabled(self) -> bool:
        return self._stripe is not None

    def create_checkout_session(
        self,
        *,
        workspace_id: str,
        plan: str,
        success_url: str,
        cancel_url: str,
        customer_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Stripe is not configured (set STRIPE_SECRET_KEY)")
        limits = get_plan_limits(plan)
        if limits.price_usd_monthly <= 0 and plan != "enterprise":
            raise ValueError(f"Plan '{plan}' is not a paid plan")

        price_id = os.getenv(limits.stripe_price_id_env, "").strip()
        if not price_id:
            # Fallback: price_data for simple setups
            session = self._stripe.checkout.Session.create(
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=customer_email,
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": limits.price_usd_monthly * 100,
                        "recurring": {"interval": "month"},
                        "product_data": {"name": f"Kanyoza {limits.name.title()}"},
                    },
                    "quantity": 1,
                }],
                metadata={"workspace_id": workspace_id, "plan": limits.name},
                client_reference_id=workspace_id,
            )
        else:
            session = self._stripe.checkout.Session.create(
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=customer_email,
                line_items=[{"price": price_id, "quantity": 1}],
                metadata={"workspace_id": workspace_id, "plan": limits.name},
                client_reference_id=workspace_id,
            )
        return {"id": session.id, "url": session.url, "workspace_id": workspace_id, "plan": limits.name}

    def handle_webhook(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Stripe is not configured")
        event = self._stripe.Webhook.construct_event(
            payload, sig_header, self._webhook_secret
        ) if self._webhook_secret else self._stripe.Event.construct_from(
            __import__("json").loads(payload), self._stripe.api_key
        )

        etype = event["type"] if isinstance(event, dict) else event.type
        data = event["data"]["object"] if isinstance(event, dict) else event.data.object

        if etype in ("checkout.session.completed", "customer.subscription.updated"):
            meta = data.get("metadata") or {}
            workspace_id = meta.get("workspace_id") or data.get("client_reference_id")
            plan = meta.get("plan") or "pro"
            if workspace_id:
                metabolism.apply_plan_to_organism(workspace_id, plan)
                return {"handled": True, "workspace_id": workspace_id, "plan": plan, "event": etype}

        if etype in ("customer.subscription.deleted",):
            meta = data.get("metadata") or {}
            workspace_id = meta.get("workspace_id")
            if workspace_id:
                metabolism.apply_plan_to_organism(workspace_id, "free")
                return {"handled": True, "workspace_id": workspace_id, "plan": "free", "event": etype}

        return {"handled": False, "event": etype}


stripe_billing = StripeBilling()
