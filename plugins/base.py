from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """Abstract social-media plugin.

    Each platform (Facebook, LinkedIn, Twitter, Discord …) implements this
    interface. The PluginRegistry discovers them and the lifecycle starts/stops
    them uniformly.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def start(self) -> None:
        """Called once at application startup."""

    @abstractmethod
    def stop(self) -> None:
        """Called once at graceful shutdown."""

    @abstractmethod
    def publish(self, content: str, image_url: Optional[str] = None, **kwargs) -> dict[str, Any]:
        """Publish *content* to this platform. Returns platform-specific response dict."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return a dict with at least {'ok': bool, 'platform': str}."""


class PluginRegistry:
    """Holds all registered plugins and exposes uniform publish/health APIs."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._lock = threading.Lock()

    def register(self, plugin: Plugin) -> None:
        with self._lock:
            self._plugins[plugin.name] = plugin
        logger.info("PluginRegistry: registered '%s'", plugin.name)

    def get(self, name: str) -> Optional[Plugin]:
        with self._lock:
            return self._plugins.get(name)

    def start_all(self) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p.start()
                logger.info("Plugin '%s' started", p.name)
            except Exception as exc:
                logger.error("Plugin '%s' failed to start: %s", p.name, exc)

    def stop_all(self) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p.stop()
                logger.info("Plugin '%s' stopped", p.name)
            except Exception as exc:
                logger.error("Plugin '%s' failed to stop: %s", p.name, exc)

    def publish_all(
        self, content: str, image_url: Optional[str] = None, **kwargs
    ) -> dict[str, dict]:
        results: dict[str, dict] = {}
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                results[p.name] = p.publish(content, image_url=image_url, **kwargs)
            except Exception as exc:
                logger.error("Plugin '%s' publish failed: %s", p.name, exc)
                results[p.name] = {"ok": False, "error": str(exc)}
        return results

    def health_all(self) -> dict[str, dict]:
        with self._lock:
            plugins = list(self._plugins.values())
        return {p.name: p.health() for p in plugins}

    @property
    def names(self) -> list[str]:
        with self._lock:
            return list(self._plugins.keys())


_registry: Optional[PluginRegistry] = None
_registry_lock = threading.Lock()


def get_plugin_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PluginRegistry()
    return _registry
