"""
Plugin Base v2.0 — Abstract social-media plugin interface.

v2.0 UPGRADES:
  - EventBus integration (publish plugin lifecycle events)
  - Health aggregation with overall status
  - Retry-aware publish with optional circuit breaker reference
  - Plugin statistics tracking
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ABSTRACT PLUGIN
# ═══════════════════════════════════════════════════════════════════════════

class Plugin(ABC):
    """Abstract social-media plugin.

    Each platform (Facebook, LinkedIn, Twitter, Discord …) implements this
    interface. The PluginRegistry discovers them and the lifecycle starts/stops
    them uniformly.

    Plugins may optionally expose a ``connector`` property that returns a
    ``SocialConnector`` instance for richer operations (delete, edit, health
    with latency, comments fetching).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def start(self) -> None:
        """Called once at application startup."""

    @abstractmethod
    def stop(self) -> None:
        """Called once at graceful shutdown."""

    @abstractmethod
    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish content to this platform.

        Returns platform-specific response dict.
        """

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return a dict with at least
        {'ok': bool, 'platform': str}.
        """

    @property
    def connector(self) -> Optional[Any]:
        """Return a SocialConnector if this plugin wraps one, else None."""
        return None

    def get_stats(self) -> dict:
        """Override to return plugin-specific statistics."""
        return {
            "name": self.name,
            "running": getattr(self, "_running", False),
        }


# ═══════════════════════════════════════════════════════════════════════════
# PLUGIN REGISTRY v2.0
# ═══════════════════════════════════════════════════════════════════════════

class PluginRegistry:
    """Holds all registered plugins and exposes uniform publish/health APIs.

    v2.0: EventBus integration, health aggregation, retry support.
    """

    # Maximum retries for publish attempts
    MAX_PUBLISH_RETRIES = 2
    # Delay between retries (seconds)
    RETRY_DELAY = 1.0

    def __init__(self, event_bus=None) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._lock = threading.Lock()
        self._event_bus = event_bus
        self._publish_count = 0
        self._publish_failures = 0

    # ═══════════════════════════════════════════════════════════════════
    # EVENT BUS
    # ═══════════════════════════════════════════════════════════════════

    def set_event_bus(self, event_bus) -> None:
        """Wire the EventBus after initialization."""
        self._event_bus = event_bus

    def _emit(
        self, event_name: str, payload: dict
    ) -> None:
        """Publish a plugin lifecycle event (async-safe).

        UnifiedEventBus.publish is async — schedule it if a loop is running;
        never leave a bare coroutine unawaited.
        """
        if not self._event_bus:
            return
        try:
            result = None
            if hasattr(self._event_bus, "publish"):
                result = self._event_bus.publish(event_name, payload)
            elif hasattr(self._event_bus, "emit"):
                result = self._event_bus.emit(event_name, payload)
            else:
                return

            import asyncio
            import inspect
            if inspect.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    # No running loop — run synchronously in a temporary loop
                    asyncio.run(result)
        except Exception as exc:
            logger.debug(
                "PluginRegistry event failed: %s",
                exc,
            )

    # ═══════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ═══════════════════════════════════════════════════════════════════

    def register(self, plugin: Plugin) -> None:
        with self._lock:
            self._plugins[plugin.name] = plugin
        logger.info(
            "PluginRegistry: registered '%s'",
            plugin.name,
        )
        self._emit("plugin.registered", {
            "name": plugin.name,
        })

    def unregister(self, name: str) -> None:
        with self._lock:
            if name in self._plugins:
                del self._plugins[name]
                logger.info(
                    "PluginRegistry: unregistered '%s'",
                    name,
                )
                self._emit("plugin.unregistered", {
                    "name": name,
                })

    def get(self, name: str) -> Optional[Plugin]:
        with self._lock:
            return self._plugins.get(name)

    def get_all(self) -> list[Plugin]:
        with self._lock:
            return list(self._plugins.values())

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start_all(self) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p.start()
                logger.info(
                    "Plugin '%s' started", p.name
                )
                self._emit("plugin.started", {
                    "name": p.name,
                })
            except Exception as exc:
                logger.error(
                    "Plugin '%s' failed to start: %s",
                    p.name,
                    exc,
                )
                self._emit("plugin.start_failed", {
                    "name": p.name,
                    "error": str(exc),
                })

    def stop_all(self) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p.stop()
                logger.info(
                    "Plugin '%s' stopped", p.name
                )
                self._emit("plugin.stopped", {
                    "name": p.name,
                })
            except Exception as exc:
                logger.error(
                    "Plugin '%s' failed to stop: %s",
                    p.name,
                    exc,
                )
                self._emit("plugin.stop_failed", {
                    "name": p.name,
                    "error": str(exc),
                })

    # ═══════════════════════════════════════════════════════════════════
    # PUBLISHING
    # ═══════════════════════════════════════════════════════════════════

    def publish_all(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        brand: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, dict]:
        """Publish to all registered plugins.

        v2.0: Retry on failure with backoff.
        """
        results: dict[str, dict] = {}
        with self._lock:
            plugins = list(self._plugins.values())

        for p in plugins:
            results[p.name] = self._publish_with_retry(
                p,
                content,
                image_url=image_url,
                image_bytes=image_bytes,
                brand=brand,
                **kwargs,
            )

        return results

    def publish_to(
        self,
        plugin_name: str,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        brand: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish to a specific plugin by name."""
        plugin = self.get(plugin_name)
        if not plugin:
            return {
                "ok": False,
                "error": f"Plugin '{plugin_name}' not found",
            }

        return self._publish_with_retry(
            plugin,
            content,
            image_url=image_url,
            image_bytes=image_bytes,
            brand=brand,
            **kwargs,
        )

    def _publish_with_retry(
        self,
        plugin: Plugin,
        content: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Publish to a single plugin with retry on failure."""
        last_error = None

        for attempt in range(
            self.MAX_PUBLISH_RETRIES + 1
        ):
            try:
                result = plugin.publish(
                    content, **kwargs
                )
                self._publish_count += 1

                self._emit("plugin.published", {
                    "plugin": plugin.name,
                    "ok": result.get("ok", False),
                    "attempt": attempt + 1,
                })

                return result

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Plugin '%s' publish failed "
                    "(attempt %d/%d): %s",
                    plugin.name,
                    attempt + 1,
                    self.MAX_PUBLISH_RETRIES + 1,
                    exc,
                )

                if (
                    attempt
                    < self.MAX_PUBLISH_RETRIES
                ):
                    time.sleep(self.RETRY_DELAY)

        self._publish_failures += 1

        self._emit("plugin.publish_failed", {
            "plugin": plugin.name,
            "error": str(last_error),
        })

        return {
            "ok": False,
            "error": str(last_error),
        }

    # ═══════════════════════════════════════════════════════════════════
    # HEALTH
    # ═══════════════════════════════════════════════════════════════════

    def health_all(self) -> dict[str, dict]:
        with self._lock:
            plugins = list(self._plugins.values())

        health = {}
        for p in plugins:
            try:
                health[p.name] = p.health()
            except Exception as exc:
                health[p.name] = {
                    "ok": False,
                    "error": str(exc),
                    "platform": p.name,
                }

        return health

    def health_aggregated(self) -> dict:
        """Return aggregated health across all plugins."""
        health = self.health_all()
        total = len(health)
        healthy = sum(
            1 for h in health.values() if h.get("ok")
        )

        return {
            "total_plugins": total,
            "healthy": healthy,
            "unhealthy": total - healthy,
            "status": (
                "healthy"
                if healthy == total
                else "degraded"
                if healthy > 0
                else "unhealthy"
            ),
            "plugins": health,
        }

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return registry statistics."""
        with self._lock:
            plugin_count = len(self._plugins)

        return {
            "plugins_registered": plugin_count,
            "publish_count": self._publish_count,
            "publish_failures": self._publish_failures,
            "publish_success_rate": (
                round(
                    (
                        self._publish_count
                        - self._publish_failures
                    )
                    / max(self._publish_count, 1)
                    * 100,
                    1,
                )
            ),
            "plugin_names": self.names,
        }

    # ═══════════════════════════════════════════════════════════════════
    # PROPERTIES
    # ═══════════════════════════════════════════════════════════════════

    @property
    def names(self) -> list[str]:
        with self._lock:
            return list(self._plugins.keys())


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_registry: Optional[PluginRegistry] = None
_registry_lock = threading.Lock()


def get_plugin_registry(
    event_bus=None,
) -> PluginRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PluginRegistry(
                    event_bus=event_bus
                )
    return _registry
