"""Capability declarations for external connectors.

A connector may accept an idempotency key as an application-level correlation
value without the provider guaranteeing native idempotency.  These explicit
capabilities prevent KDO from confusing the two.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorCapabilities:
    native_idempotency: bool = False
    authoritative_lookup: bool = False
    safe_retry_after_ambiguous: bool = False


def capabilities(connector) -> ConnectorCapabilities:
    value = getattr(connector, "CAPABILITIES", None)
    if isinstance(value, ConnectorCapabilities):
        return value
    return ConnectorCapabilities(
        native_idempotency=bool(getattr(connector, "NATIVE_IDEMPOTENCY", False)),
        authoritative_lookup=callable(getattr(connector, "lookup_post", None)),
        safe_retry_after_ambiguous=bool(getattr(connector, "SAFE_RETRY_AFTER_AMBIGUOUS", False)),
    )
