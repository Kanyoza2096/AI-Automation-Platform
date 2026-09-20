from __future__ import annotations

from dataclasses import dataclass
from .policies import AffiliateDisclosurePolicy


@dataclass(frozen=True)
class DisclosureDecision:
    allowed: bool
    content: str
    disclosure_added: bool
    reason: str = ""


def enforce_disclosure(content: str, policy: AffiliateDisclosurePolicy) -> DisclosureDecision:
    policy.validate()
    text = str(content or "").strip()
    if not policy.required:
        return DisclosureDecision(True, text, False)
    markers = ("affiliate link", "affiliate links", "commission", "may earn")
    if any(m in text.lower() for m in markers):
        return DisclosureDecision(True, text, False)
    if policy.placement == "caption_start":
        final = f"{policy.disclosure_text}\n\n{text}" if text else policy.disclosure_text
    else:
        final = f"{text}\n\n{policy.disclosure_text}" if text else policy.disclosure_text
    return DisclosureDecision(True, final, True)
