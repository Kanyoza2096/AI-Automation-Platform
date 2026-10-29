"""V1.5 Business Understanding -> Playbook -> Provisioning kernel.

The LLM is an untrusted interpreter. Playbooks and provisioning are
deterministic and workspace-scoped. Provisioning never grants execution
authority; worker identity, permissions, policy, approvals and Action
Kernel remain separate.

V1.5.1 (2026-10-28) — SIGNATURE FIX FOR GATEWAY CALL:

  Removed the `capability="structured_json"` kwarg from the
  `complete_async_structured()` call. That method hardcodes capability
  internally; passing it explicitly raised:

      LLMGateway.complete_structured() got an unexpected keyword argument
      'capability'

  Every understand_business call was falling through to the deterministic
  fallback because of this TypeError.

V1.5 (2026-10-27) — INTERNAL GATEWAY EXTRACTION:

  `understand_business` previously reached an external OpenAI-compatible
  endpoint gated on KDO_AI_BASE_URL and KDO_AI_API_KEY. Neither was ever
  set in production, so every call returned the deterministic fallback
  (source="deterministic", confidence=0.55, generic goals, empty audience).

  Now: calls the platform's internal LLM gateway directly via
  get_llm_gateway().complete_async_structured(). No external HTTP, no
  env vars, no proxy. Same gateway used by content_engine, tool_agent,
  and the Director.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("kanyoza.services.business_onboarding")

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbooks"
SUPPORTED = {"creative_agency", "coffee_shop", "generic"}


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BusinessProfile:
    business_type: str
    industry: str
    size: str
    location: str
    goals: tuple[str, ...]
    channels: tuple[str, ...]
    products: tuple[str, ...]
    audience: str
    confidence: float
    source: str


@dataclass(frozen=True)
class Playbook:
    business_type: str
    industry: str
    default_workers: tuple[dict[str, Any], ...]
    default_connectors: tuple[str, ...]
    default_content_pillars: tuple[str, ...]
    default_rules: tuple[dict[str, Any], ...]


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic fallback (keyword-based extraction)
# ═══════════════════════════════════════════════════════════════════════════


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _fallback_profile(description: str) -> BusinessProfile:
    """Deterministic keyword-based extraction. Always available."""
    t = _tokens(description)
    agency = bool(t & {"agency", "photography", "photographer", "video", "editing", "creative"})
    coffee = bool(t & {"coffee", "cafe", "café", "shop", "regulars", "customers"}) and bool(t & {"coffee", "cafe", "café"})
    if agency:
        bt, industry = "creative_agency", "media_services"
        products = tuple(
            x for x, present in [
                ("photography", "photograph" in t or "photography" in t),
                ("video_editing", "video" in t or "editing" in t),
                ("social_media_management", "social" in t),
            ] if present
        )
    elif coffee:
        bt, industry = "coffee_shop", "food_and_beverage"
        products = ("coffee", "food_and_beverage")
    else:
        bt, industry = "generic", "general"
        products = ()
    goals = []
    mapping = [
        ("acquire_more_clients", {"clients", "client", "acquire", "customers", "customer"}),
        ("consistent_social_presence", {"post", "posting", "social", "instagram"}),
        ("lead_follow_up", {"lead", "follow", "followup", "follow_up"}),
        ("customer_retention", {"regulars", "retention", "repeat", "back"}),
        ("website_presence", {"website", "site"}),
    ]
    for goal, words in mapping:
        if t & words:
            goals.append(goal)
    channels = [x for x in ("instagram", "linkedin", "whatsapp", "website") if x in t]
    if bt == "creative_agency" and not channels:
        channels = ["instagram", "linkedin", "website", "whatsapp"]
    if bt == "coffee_shop" and not channels:
        channels = ["instagram", "website", "whatsapp"]
    return BusinessProfile(
        bt, industry,
        "micro" if len(description) < 1000 else "small",
        "", tuple(dict.fromkeys(goals)), tuple(channels), products, "",
        0.55, "deterministic",
    )


# ═══════════════════════════════════════════════════════════════════════════
# LLM-backed extraction (via internal gateway)
# ═══════════════════════════════════════════════════════════════════════════


async def understand_business(description: str) -> BusinessProfile:
    """Extract a business profile via the internal LLM gateway.

    Deterministic extraction always runs first as a baseline. If the
    gateway returns a valid structured payload, the LLM-extracted profile
    is returned (source="ai"). Otherwise the deterministic fallback is
    returned (source="deterministic").

    The LLM path is not gated on any env var — the gateway is a
    first-class platform service. If the caller is not in the gateway's
    LLM_ALLOWED_CALLERS list, the gateway returns ok=False and we log a
    warning. Add "business_onboarding" to _DEFAULT_ALLOWED_CALLERS in
    ai/gateway.py to enable.
    """
    description = str(description or "").strip()
    if not description:
        raise ValueError("business description is required")

    fallback = _fallback_profile(description)

    try:
        from ai.gateway import get_llm_gateway
    except Exception as exc:
        logger.warning(
            "understand_business: gateway import failed (%s) — deterministic fallback",
            exc,
        )
        return fallback

    schema = {
        "business_type": "creative_agency|coffee_shop|generic",
        "industry": "string",
        "size": "micro|small|medium|unknown",
        "location": "string",
        "goals": ["string"],
        "channels": ["instagram|linkedin|whatsapp|website|email|other"],
        "products": ["string"],
        "audience": "string",
        "confidence": 0.0,
    }
    prompt = (
        "Extract a business profile from the user's description. "
        "Return JSON only. Never invent facts. Use generic when uncertain. "
        "Schema: " + json.dumps(schema) + "\n\n"
        "Description:\n" + description[:6000]
    )

    try:
        # NOTE: complete_async_structured() hardcodes capability internally.
        # Do NOT pass capability="structured_json" — the method does not
        # accept it and every call would raise TypeError.
        result = await get_llm_gateway().complete_async_structured(
            prompt,
            required_keys=["business_type", "goals", "channels"],
            caller="business_onboarding",
            temperature=0,
            max_tokens=1500,
        )
    except Exception as exc:
        logger.warning(
            "understand_business: gateway call raised (%s) — deterministic fallback",
            exc,
        )
        return fallback

    if not result.ok:
        logger.warning(
            "understand_business: gateway declined (reason=%s mode=%s) "
            "— deterministic fallback",
            result.reason,
            getattr(result.mode, "value", result.mode),
        )
        return fallback

    data = result.data or {}
    if not isinstance(data, dict):
        logger.warning(
            "understand_business: gateway returned non-dict (%s) — deterministic fallback",
            type(data).__name__,
        )
        return fallback

    bt = str(data.get("business_type") or "generic").strip().lower()
    if bt not in SUPPORTED:
        bt = "generic"

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.5

    logger.info(
        "understand_business: LLM extraction ok type=%s confidence=%.2f "
        "provider=%s model=%s latency_ms=%.0f",
        bt, confidence, result.provider, result.model, result.latency_ms,
    )

    return BusinessProfile(
        bt,
        str(data.get("industry") or fallback.industry)[:120],
        str(data.get("size") or "unknown"),
        str(data.get("location") or "")[:120],
        tuple(str(x) for x in data.get("goals") or [])[:20],
        tuple(str(x) for x in data.get("channels") or [])[:20],
        tuple(str(x) for x in data.get("products") or [])[:20],
        str(data.get("audience") or "")[:500],
        confidence,
        "ai",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Playbook loading + plan construction (deterministic)
# ═══════════════════════════════════════════════════════════════════════════


def load_playbook(business_type: str) -> Playbook:
    safe = business_type if business_type in SUPPORTED else "generic"
    data = yaml.safe_load((PLAYBOOK_DIR / f"{safe}.yaml").read_text(encoding="utf-8"))
    return Playbook(
        str(data["business_type"]),
        str(data.get("industry") or "general"),
        tuple(data.get("default_workers") or ()),
        tuple(data.get("default_connectors") or ()),
        tuple(data.get("default_content_pillars") or ()),
        tuple(data.get("default_rules") or ()),
    )


def build_business_plan(profile: BusinessProfile, playbook: Playbook) -> dict[str, Any]:
    goals = set(profile.goals)
    workers = [
        w for w in playbook.default_workers
        if not (
            w["role"] == "customer_retention"
            and "customer_retention" not in goals
            and profile.business_type != "coffee_shop"
        )
    ]
    connectors = list(playbook.default_connectors)
    return {
        "plan_id": "plan_" + uuid.uuid4().hex[:20],
        "business_profile": profile.__dict__ | {
            "goals": list(profile.goals),
            "channels": list(profile.channels),
            "products": list(profile.products),
        },
        "playbook": playbook.business_type,
        "workers": workers,
        "connectors": connectors,
        "content_pillars": list(playbook.default_content_pillars),
        "rules": list(playbook.default_rules),
        "readiness": "needs_connectors" if connectors else "ready",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Kernel (workspace-scoped, deterministic persistence)
# ═══════════════════════════════════════════════════════════════════════════


class BusinessOnboardingKernel:
    """Deterministic provisioning kernel. DB client is required."""

    def __init__(self, db):
        if db is None:
            raise ValueError(
                "BusinessOnboardingKernel requires a non-None db client. "
                "See BusinessOnboardingServiceAdapter.__init__ for the "
                "resolution chain."
            )
        self.db = db

    async def plan(self, workspace_id: str, description: str) -> dict[str, Any]:
        profile = await understand_business(description)
        playbook = load_playbook(profile.business_type)
        return build_business_plan(profile, playbook)

    def provision(
        self,
        workspace_id: str,
        plan: dict[str, Any],
        *,
        activate: bool = False,
    ) -> dict[str, Any]:
        ws = str(workspace_id)
        profile = plan["business_profile"]
        plan_id = str(plan["plan_id"])

        # Idempotency: replay by plan_id
        existing = (
            self.db.table("business_onboarding_plans")
            .select("*")
            .eq("workspace_id", ws)
            .eq("plan_id", plan_id)
            .maybe_single()
            .execute()
        )
        if getattr(existing, "data", None):
            return {"plan": existing.data, "replayed": True}

        self.db.table("business_onboarding_plans").insert({
            "plan_id": plan_id,
            "workspace_id": ws,
            "business_type": profile["business_type"],
            "profile": profile,
            "plan": plan,
            "status": "provisioning",
        }).execute()

        provisioned = {"workers": [], "rules": [], "brand": None}

        # Brand
        slug = re.sub(
            r"[^a-z0-9]+", "-",
            profile.get("business_type", "business").lower(),
        ).strip("-") or "business"
        brand_id = "onboard_" + uuid.uuid4().hex[:16]
        self.db.table("brands").insert({
            "id": brand_id,
            "workspace_id": ws,
            "name": profile.get("business_type", "Business").replace("_", " ").title(),
            "slug": f"{slug}-{brand_id[-6:]}",
            "tone": "professional, warm",
            "language": "en",
            "audience_description": profile.get("audience") or "",
            "topic_categories": plan.get("content_pillars", []),
            "custom_topics": {},
            "posting_schedule": {"timezone": "UTC", "max_posts_per_day": 3},
            "status": "active",
            "metadata": {"onboarding_plan_id": plan_id},
        }).execute()
        provisioned["brand"] = brand_id

        # Workers
        for worker in plan.get("workers", []):
            aid = f"{worker['role']}:{ws}"
            row = {
                "workspace_id": ws,
                "agent_id": aid,
                "name": worker["role"].replace("_", " ").title(),
                "purpose": worker["purpose"],
                "agent_class": "business_worker",
                "version": "1",
                "status": "proposed",
                "declared_capabilities": worker.get("capabilities", []),
                "metadata": {"onboarding_plan_id": plan_id, "role": worker["role"]},
                "identity_fingerprint": "onboarding-provisioned",
            }
            self.db.table("agent_identities").upsert(
                row, on_conflict="workspace_id,agent_id"
            ).execute()
            provisioned["workers"].append(aid)

        # Rules
        for rule in plan.get("rules", []):
            rid = f"{plan_id}:{rule['id']}"
            self.db.table("business_automation_rules").upsert({
                "rule_id": rid,
                "workspace_id": ws,
                "name": rule["name"],
                "enabled": False,
                "condition": {},
                "action": rule["action"],
                "parameters": {
                    "trigger": rule["trigger"],
                    "onboarding_plan_id": plan_id,
                },
                "risk": rule.get("risk", "medium"),
                "version": 1,
            }).execute()
            provisioned["rules"].append(rid)

        status = "needs_connectors" if plan.get("connectors") else "ready"
        self.db.table("business_onboarding_plans").update({
            "status": status,
            "provisioned": provisioned,
        }).eq("workspace_id", ws).eq("plan_id", plan_id).execute()

        return {
            "plan_id": plan_id,
            "status": status,
            "provisioned": provisioned,
            "required_connectors": plan.get("connectors", []),
            "activation_allowed": bool(activate and status == "ready"),
        }
