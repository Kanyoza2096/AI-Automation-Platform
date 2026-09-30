from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
import ipaddress


@dataclass(frozen=True)
class AffiliateDisclosurePolicy:
    required: bool = True
    disclosure_text: str = "Affiliate link: I may earn a commission if you purchase through this link."
    placement: str = "near_link"
    language: str = "en"
    jurisdiction: str = "global"

    def validate(self) -> None:
        if self.placement not in {"near_link", "caption_start", "caption_end"}:
            raise ValueError("unsupported disclosure placement")
        if self.required and not self.disclosure_text.strip():
            raise ValueError("disclosure_text is required when disclosure is enabled")


def normalize_domain(value: str) -> str:
    value = str(value or "").strip().lower().rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    if not value or any(c.isspace() for c in value) or "/" in value or ":" in value:
        raise ValueError("invalid affiliate domain")
    return value


def validate_destination(url: str, allowed_domains: list[str] | tuple[str, ...]) -> str:
    """Validate a browser redirect target without turning /go into an open redirect."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("affiliate destinations must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("affiliate destinations cannot contain credentials")
    if parsed.port not in (None, 443):
        raise ValueError("affiliate destinations cannot use non-standard ports")
    host = normalize_domain(parsed.hostname)
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            raise ValueError("affiliate destination cannot target a private or reserved IP")
    except ValueError as exc:
        # Normal DNS names are expected; only reject literal IPs that are unsafe.
        if "private or reserved" in str(exc):
            raise
    allowed = {normalize_domain(d) for d in allowed_domains}
    if not allowed:
        raise ValueError("affiliate destination requires an approved domain")
    if host not in allowed and not any(host.endswith("." + d) for d in allowed):
        raise ValueError("affiliate destination host is not approved")
    return raw
