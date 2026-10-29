from __future__ import annotations
from dataclasses import dataclass
from domain.business_activation.facts import FactType

CORE = [
    FactType.BUSINESS_IDENTITY, FactType.BUSINESS_DESCRIPTION,
    FactType.PRODUCT_SERVICE, FactType.CUSTOMER_SEGMENT,
    FactType.LOCATION, FactType.CONTACT_CHANNEL,
]

@dataclass(frozen=True)
class DiscoveryProfile:
    name: str
    signals: tuple[str, ...]
    requirements: tuple[FactType, ...]

# These are planning hints, not sector truth. The Knowledge Plane remains the
# authority and explicit confirmation remains mandatory.
PROFILES = (
    DiscoveryProfile("commerce", ("shop", "store", "retail", "sell", "ecommerce", "e-commerce", "market"),
                     (FactType.PRICING, FactType.PAYMENT_METHOD, FactType.FULFILLMENT, FactType.REFUND_POLICY)),
    DiscoveryProfile("food_hospitality", ("restaurant", "cafe", "café", "hotel", "lodging", "catering", "food", "bar"),
                     (FactType.BUSINESS_HOURS, FactType.PRICING, FactType.PAYMENT_METHOD, FactType.FULFILLMENT, FactType.REFUND_POLICY)),
    DiscoveryProfile("logistics", ("delivery", "courier", "logistics", "shipping", "transport", "dispatch", "freight"),
                     (FactType.DELIVERY_AREA, FactType.PRICING, FactType.PAYMENT_METHOD, FactType.FULFILLMENT, FactType.BUSINESS_HOURS)),
    DiscoveryProfile("professional_services", ("consulting", "agency", "law", "legal", "accounting", "accountant", "marketing", "software", "development"),
                     (FactType.PRICING, FactType.BUSINESS_HOURS, FactType.PAYMENT_METHOD, FactType.ESCALATION_RULE)),
    DiscoveryProfile("education", ("school", "college", "university", "training", "academy", "course", "education"),
                     (FactType.BUSINESS_HOURS, FactType.PRICING, FactType.CONTACT_CHANNEL, FactType.ESCALATION_RULE)),
    DiscoveryProfile("healthcare", ("clinic", "hospital", "medical", "health", "pharmacy", "doctor", "dental"),
                     (FactType.BUSINESS_HOURS, FactType.CONTACT_CHANNEL, FactType.PRICING, FactType.ESCALATION_RULE)),
)

class BusinessDiscoveryPlanner:
    """Deterministic, explainable R13 planner.

    It chooses a minimum useful discovery scope from the business description
    and existing confirmed facts. It never promotes a candidate to truth.
    """
    def infer(self, text: str, explicit_scope: list[FactType] | None = None) -> tuple[list[FactType], list[str]]:
        if explicit_scope:
            return list(dict.fromkeys(explicit_scope)), ["explicit_scope"]
        lowered = text.lower()
        scope = list(CORE)
        reasons: list[str] = ["core_business_model"]
        for profile in PROFILES:
            hits = [signal for signal in profile.signals if signal in lowered]
            if hits:
                scope.extend(profile.requirements)
                reasons.append(f"profile:{profile.name}:{','.join(hits[:4])}")
        # General operational signals work across sectors.
        universal = (
            (FactType.PAYMENT_METHOD, ("payment", "pay", "cash", "bank", "mobile money")),
            (FactType.DELIVERY_AREA, ("deliver", "delivery", "ship", "shipping")),
            (FactType.INTEGRATION, ("api", "integration", "integrated", "shopify", "stripe")),
            (FactType.STAFF_ROLE, ("staff", "employee", "team", "manager")),
        )
        for fact_type, signals in universal:
            if any(signal in lowered for signal in signals):
                scope.append(fact_type)
                reasons.append(f"signal:{fact_type.value}")
        return list(dict.fromkeys(scope)), reasons
