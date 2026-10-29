"""Unified provider publishing contract and capability validation.

The canonical pipeline owns policy; providers own transport.  This module is
intentionally deterministic and side-effect free so unsupported requests are
rejected before an external API call is attempted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ProviderContract:
    platform: str
    max_text_length: Optional[int] = None
    supports_image_url: bool = False
    supports_image_bytes: bool = False
    requires_image_url: bool = False
    requires_recipient: bool = False
    supports_threads: bool = False
    receipt_kind: str = "post_id"
    reconciliation: str = "lookup"


CONTRACTS = {
    "facebook": ProviderContract("facebook", max_text_length=63206, supports_image_url=True, supports_image_bytes=True, receipt_kind="post_id", reconciliation="lookup"),
    "instagram": ProviderContract("instagram", max_text_length=2200, supports_image_url=True, requires_image_url=True, receipt_kind="media_id", reconciliation="lookup"),
    "twitter": ProviderContract("twitter", max_text_length=280, supports_image_bytes=True, supports_threads=True, receipt_kind="post_id", reconciliation="lookup_or_thread"),
    "linkedin": ProviderContract("linkedin", max_text_length=3000, supports_image_bytes=True, receipt_kind="post_id", reconciliation="lookup"),
    "telegram": ProviderContract("telegram", max_text_length=4096, supports_image_url=True, supports_image_bytes=True, receipt_kind="message_id", reconciliation="receipt"),
    "whatsapp": ProviderContract("whatsapp", max_text_length=4096, supports_image_url=True, requires_recipient=True, receipt_kind="message_id", reconciliation="webhook"),
}


def get_contract(platform: str) -> Optional[ProviderContract]:
    return CONTRACTS.get((platform or "").strip().lower())


def validate_request(
    platform: str,
    *,
    text: str = "",
    image_url: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    recipient: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return a deterministic error dict or None when the request is valid."""
    p = (platform or "").strip().lower()
    contract = get_contract(p)
    if contract is None:
        return {"error_code": "unsupported_platform", "error": f"No publishing contract for platform '{p}'"}

    if contract.max_text_length is not None and len(text or "") > contract.max_text_length and not contract.supports_threads:
        return {"error_code": "content_too_long", "error": f"{p} content exceeds {contract.max_text_length} characters"}

    if image_url and not contract.supports_image_url and not image_bytes:
        return {"error_code": "media_url_unsupported", "error": f"{p} does not accept image_url media"}
    if image_bytes and not contract.supports_image_bytes:
        return {"error_code": "media_bytes_unsupported", "error": f"{p} does not accept raw image bytes"}
    if contract.requires_image_url and not image_url:
        return {"error_code": "image_url_required", "error": f"{p} requires a publicly accessible image_url"}
    if contract.requires_recipient and not recipient:
        return {"error_code": "recipient_required", "error": f"{p} requires a recipient"}

    return None
