from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List
from urllib.parse import urlparse
import ipaddress
import socket

from services.web_intelligence.models import WebPage, WebResult



def validate_external_url(url: str) -> tuple[bool, str]:
    """Validate an outbound URL and reject local/private network targets."""
    from urllib.parse import urlparse
    import ipaddress, socket
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "invalid_url"
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
            return False, "blocked_internal_hostname"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        if not addresses:
            return False, "host_resolution_failed"
        for addr in addresses:
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified:
                return False, "blocked_private_address"
        return True, "ok"
    except (OSError, ValueError):
        return False, "host_resolution_failed"


class WebKnowledgeProvider(ABC):
    name: str = "base"

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[WebResult]:
        ...

    def fetch(self, url: str) -> WebPage:
        """Optional fetch; default HTTP GET + strip tags lightly."""
        import re
        import requests

        try:
            allowed, reason = validate_external_url(url)
            if not allowed:
                return WebPage(url=url, error=reason, source=self.name)
            r = requests.get(
                url,
                timeout=(5, 15),
                allow_redirects=False,
                headers={"User-Agent": "KanyozaWebIntelligence/1.0", "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
                stream=True,
            )
            if 300 <= r.status_code < 400:
                return WebPage(url=url, error="redirect_not_allowed", status_code=r.status_code, source=self.name)
            content_length = r.headers.get("Content-Length")
            if content_length and int(content_length) > 5_000_000:
                return WebPage(url=url, error="response_too_large", status_code=r.status_code, source=self.name)
            raw = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                raw.extend(chunk)
                if len(raw) > 5_000_000:
                    return WebPage(url=url, error="response_too_large", status_code=r.status_code, source=self.name)
            text = raw.decode(r.encoding or "utf-8", errors="replace")
            title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            title = (title_m.group(1).strip() if title_m else "")[:200]
            # crude strip
            body = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
            body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            return WebPage(
                url=url,
                title=title,
                text=body[:8000],
                status_code=r.status_code,
                source=self.name,
            )
        except Exception as exc:
            return WebPage(url=url, error=str(exc)[:200], source=self.name)
