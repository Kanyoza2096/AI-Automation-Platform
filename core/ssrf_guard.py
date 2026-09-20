"""
SSRF protection for monitor / outbound URL fetches (architecture report §30).

Blocks:
  - non-http(s) schemes
  - localhost / loopback
  - private and link-local ranges
  - metadata endpoints (cloud)
  - optional DNS resolve to private IP
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("kanyoza.core.ssrf_guard")

_BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
})


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return bool(
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        )
    except ValueError:
        return True  # unparseable → block


def validate_public_http_url(url: str, *, resolve_dns: bool = True) -> Tuple[bool, str]:
    """
    Returns (ok, reason). ok=False means reject the URL.
    """
    if not url or not isinstance(url, str):
        return False, "empty_url"
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid_url"

    if parsed.scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "missing_host"
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return False, "blocked_host"
    # literal IP in hostname
    try:
        if _is_private_ip(host):
            return False, "private_ip_literal"
    except Exception:
        pass

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                ip = info[4][0]
                if _is_private_ip(ip):
                    return False, f"resolves_to_private:{ip}"
        except socket.gaierror:
            return False, "dns_resolution_failed"
        except Exception as exc:
            logger.debug("ssrf dns check error: %s", exc)
            return False, "dns_check_error"

    return True, "ok"


def assert_public_http_url(url: str) -> None:
    ok, reason = validate_public_http_url(url)
    if not ok:
        raise ValueError(f"URL blocked by SSRF guard: {reason}")
