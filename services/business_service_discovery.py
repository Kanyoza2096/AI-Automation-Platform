"""R13-C sector/service-aware business discovery planner.

This module is deliberately read-only. It discovers candidate sector/service
areas from business language and maps them to the already-certified sector-pack
capabilities, resources, workflows and external actions. It does not create
knowledge, authorize actions, or execute providers.
"""
from __future__ import annotations

import re
from typing import Any

SECTOR_PACKS: tuple[dict[str, Any], ...] = (
    {"id": "marketing_media", "name": "Marketing & Media", "keywords": ("marketing", "advertising", "campaign", "social media", "content", "publishing", "media", "promotion")},
    {"id": "commerce", "name": "Commerce & E-commerce", "keywords": ("shop", "store", "ecommerce", "e-commerce", "sell", "sales", "products", "orders", "inventory", "payment", "checkout")},
    {"id": "it_operations", "name": "IT Operations", "keywords": ("it", "software", "server", "deployment", "incident", "service desk", "technical support", "infrastructure", "remediation")},
    {"id": "cybersecurity", "name": "Cybersecurity Operations", "keywords": ("cybersecurity", "security", "incident response", "threat", "vulnerability", "security operations", "soc", "asset protection")},
    {"id": "crm", "name": "Customer Relationship Management", "keywords": ("crm", "customer", "customers", "leads", "follow-up", "follow up", "sales pipeline", "meetings", "client relationship")},
    {"id": "education", "name": "Education & School Operations", "keywords": ("school", "education", "students", "student", "teacher", "tuition", "fees", "parent", "academy", "college", "training")},
    {"id": "healthcare", "name": "Healthcare Administration & Hospital Operations", "keywords": ("hospital", "clinic", "healthcare", "health", "patient", "patients", "appointment", "medical", "care", "pharmacy")},
    {"id": "finance", "name": "Finance & Financial Operations", "keywords": ("finance", "financial", "accounting", "invoice", "invoicing", "statement", "reconciliation", "payments", "bookkeeping")},
    {"id": "supply_chain", "name": "Supply Chain & Logistics", "keywords": ("logistics", "courier", "delivery", "deliver", "shipping", "shipment", "warehouse", "carrier", "freight", "transport", "supply chain")},
    {"id": "manufacturing", "name": "Manufacturing & Industrial Operations", "keywords": ("manufacturing", "factory", "production", "production line", "quality control", "maintenance", "industrial")},
    {"id": "energy", "name": "Energy & Utilities", "keywords": ("energy", "utility", "utilities", "electricity", "power", "metering", "field crew", "grid")},
    {"id": "government", "name": "Government & Public Services", "keywords": ("government", "public service", "permit", "public sector", "applicant", "citizen service", "municipal")},
    {"id": "agriculture", "name": "Agriculture & Farm Operations", "keywords": ("agriculture", "farm", "farming", "farmer", "crop", "harvest", "field", "livestock", "agronomy")},
)

SERVICE_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "commerce": {
        "catalog": ("products/services offered", "product/service descriptions", "pricing", "availability"),
        "orders": ("order intake process", "order statuses", "customer requirements", "cancellation rules"),
        "payments": ("accepted payment methods", "payment terms", "refund policy"),
        "fulfillment": ("fulfillment process", "service/delivery areas", "delivery times", "exceptions"),
    },
    "supply_chain": {
        "shipment_management": ("shipment types", "service areas", "delivery timelines", "tracking process"),
        "inventory_operations": ("inventory locations", "stock rules", "reorder rules"),
        "carrier_governance": ("carriers", "carrier selection rules", "carrier service levels"),
        "exception_management": ("delivery exception rules", "escalation process", "resolution policy"),
    },
    "healthcare": {
        "patient_administration": ("patient intake process", "service types", "eligibility requirements"),
        "appointments": ("appointment types", "operating hours", "booking/cancellation rules"),
        "billing": ("billing rules", "payment methods", "insurance/payment terms"),
        "patient_communication": ("approved communication channels", "communication policies", "escalation rules"),
    },
    "education": {
        "student_management": ("programs/courses", "admission requirements", "student lifecycle"),
        "fee_management": ("fees", "payment schedules", "refund/withdrawal rules"),
        "parent_communication": ("communication channels", "parent notification rules", "escalation rules"),
    },
    "finance": {
        "invoicing": ("invoice rules", "payment terms", "tax requirements"),
        "payment_governance": ("payment authorization rules", "payment methods", "approval thresholds"),
        "reconciliation": ("reconciliation process", "authoritative records", "exception handling"),
    },
    "marketing_media": {
        "campaigns": ("campaign types", "target audiences", "campaign objectives"),
        "content": ("content types", "brand guidelines", "approval rules"),
        "publishing": ("publishing channels", "publishing schedule", "approval authority"),
    },
    "crm": {
        "contacts": ("customer types", "customer data sources", "contact rules"),
        "deals": ("sales stages", "qualification rules", "pricing/offer rules"),
        "followups": ("follow-up policy", "service levels", "escalation rules"),
    },
    "it_operations": {
        "service_inventory": ("services managed", "environments", "service owners"),
        "incident_management": ("incident severity rules", "response targets", "escalation process"),
        "deployment_governance": ("deployment policy", "approval rules", "rollback policy"),
    },
    "cybersecurity": {
        "asset_inventory": ("protected assets", "asset ownership", "criticality rules"),
        "incident_response": ("incident classes", "response procedures", "escalation authority"),
        "finding_management": ("vulnerability severity rules", "remediation targets", "exception policy"),
    },
    "manufacturing": {
        "production_orders": ("products manufactured", "production process", "production constraints"),
        "quality_management": ("quality standards", "inspection rules", "nonconformance process"),
        "maintenance_governance": ("maintenance policy", "equipment criticality", "maintenance schedules"),
    },
    "energy": {
        "asset_management": ("energy assets", "asset locations", "criticality"),
        "metering": ("metering model", "reading frequency", "billing/usage rules"),
        "work_order_management": ("work order process", "crew rules", "service territories"),
    },
    "government": {
        "public_service_management": ("services offered", "eligibility rules", "service standards"),
        "permit_case_management": ("permit types", "application requirements", "approval rules"),
        "field_service": ("service territories", "dispatch rules", "escalation rules"),
    },
    "agriculture": {
        "farm_management": ("farms operated", "farm locations", "operating model"),
        "crop_cycle_management": ("crops", "crop cycles", "seasonal rules"),
        "harvest_management": ("harvest process", "quality requirements", "market/delivery rules"),
    },
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _contains(text: str, keyword: str) -> bool:
    key = _norm(keyword)
    if " " in key or "-" in key:
        return key in text
    return bool(re.search(r"\b" + re.escape(key) + r"\b", text))


def _score(text: str, keywords: tuple[str, ...]) -> tuple[int, list[str]]:
    hits = [key for key in keywords if _contains(text, key)]
    return len(hits), hits


def _capability_matches(text: str, capabilities: tuple[str, ...]) -> list[str]:
    hits = []
    aliases = {
        "fulfillment": ("fulfill", "fulfillment", "delivery"),
        "orders": ("order", "orders"),
        "payments": ("payment", "payments", "checkout"),
        "appointments": ("appointment", "appointments", "booking", "bookings"),
        "publishing": ("publish", "publishing"),
        "contacts": ("contact", "contacts", "customers", "clients"),
        "shipment_management": ("shipment", "shipments", "shipping", "delivery"),
    }
    for cap in capabilities:
        terms = aliases.get(cap, (cap.replace("_", " "),))
        if any(_contains(text, term) for term in terms):
            hits.append(cap)
    return hits


def _load_manifest(pack_id: str) -> dict[str, Any] | None:
    for item in SECTOR_PACKS:
        if item["id"] != pack_id:
            continue
        module_name = f"plugins.industries.{pack_id}.plugin"
        class_name = "".join(part.title() for part in pack_id.split("_")) + "Plugin"
        try:
            from importlib import import_module
            module = import_module(module_name)
            plugin = getattr(module, class_name)(None, None)
            manifest = plugin.get_manifest()
            return {
                "pack_id": manifest.pack_id,
                "display_name": manifest.display_name,
                "capabilities": list(manifest.capabilities),
                "resources": list(manifest.resources),
                "workflows": list(manifest.workflows),
                "external_actions": list(manifest.external_actions),
            }
        except Exception:
            return None
    return None


def plan_service_discovery(*, business_text: str, explicit_sector_ids: list[str] | None = None) -> dict[str, Any]:
    """Return candidate sectors and service knowledge requirements.

    The result is planning evidence only. It never asserts that a business is
    actually in a sector, never writes Knowledge Plane facts, and never grants
    execution capability.
    """
    text = _norm(business_text)
    explicit = {str(x) for x in (explicit_sector_ids or []) if str(x)}
    candidates: list[dict[str, Any]] = []

    for item in SECTOR_PACKS:
        score, evidence = _score(text, item["keywords"])
        manifest = _load_manifest(item["id"])
        if item["id"] in explicit:
            score = max(score, 100)
            evidence = list(dict.fromkeys(["explicit_scope", *evidence]))
        if score <= 0 and item["id"] not in explicit:
            continue
        if not manifest:
            continue
        capability_hits = _capability_matches(text, tuple(manifest["capabilities"]))
        selected_caps = capability_hits or list(manifest["capabilities"][:3])
        services = []
        for capability in selected_caps:
            reqs = SERVICE_REQUIREMENTS.get(item["id"], {}).get(capability, ())
            services.append({
                "capability": capability,
                "discovery_status": "candidate",
                "knowledge_requirements": list(reqs),
            })
        candidates.append({
            "sector_id": item["id"],
            "sector_name": manifest["display_name"],
            "match_score": score,
            "evidence": evidence,
            "candidate_services": services,
            "available_capabilities": manifest["capabilities"],
            "resources": manifest["resources"],
            "workflows": manifest["workflows"],
            "external_actions": manifest["external_actions"],
            "authority": "metadata_only",
        })

    candidates.sort(key=lambda item: (-item["match_score"], item["sector_id"]))
    return {
        "schema": "KDO.R13C.ServiceDiscoveryPlan/v1",
        "read_only": True,
        "business_text": business_text,
        "explicit_scope": sorted(explicit),
        "candidates": candidates,
        "next_step": "confirm_or_clarify_sector_and_service_candidates",
        "safety": {
            "does_not_create_knowledge": True,
            "does_not_confirm_business_facts": True,
            "does_not_authorize_actions": True,
            "does_not_execute_providers": True,
        },
    }
