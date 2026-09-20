"""Website plugin facade over the governed REST website connector."""
from __future__ import annotations

from typing import Any, Optional

from plugins.base import Plugin
from services.connectors.website_connector import WebsiteConnector


class WebsitePlugin(Plugin):
    name = "website"

    def __init__(self, social_account=None) -> None:
        self._account = social_account
        self._connector = WebsiteConnector(social_account)
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def publish(self, content: str, image_url: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        result = self._connector.publish(content, image_url=image_url, **kwargs)
        return {
            "ok": result.ok,
            "platform": result.platform,
            "post_id": result.post_id,
            "error": result.error,
            "outcome": result.outcome,
            "metadata": result.metadata,
        }

    def health(self) -> dict[str, Any]:
        result = self._connector.health()
        return {
            "ok": result.ok,
            "platform": result.platform,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "outcome": result.outcome,
            "metadata": result.metadata,
        }

    @property
    def connector(self):
        return self._connector
