"""
Kanyoza Systems AI Platform v10 — Service Container (Dependency Injection).

Design principles:
  - Services are registered once and resolved on-demand.
  - Singletons are created lazily on first resolve and cached.
  - Thread-safe — safe to resolve services from concurrent request threads.
  - No circular imports — this module imports nothing from the application layer.

Usage:
    container = ServiceContainer()
    container.register(AIService, lambda: GeminiAIService(config))
    ai = container.resolve(AIService)   # type: AIService
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Type, TypeVar

T = TypeVar("T")


class _Registration:
    """Internal registration record for a single service."""

    __slots__ = ("factory", "singleton", "_instance", "_lock")

    def __init__(self, factory: Callable[[], T], singleton: bool) -> None:
        self.factory: Callable[[], T] = factory
        self.singleton: bool = singleton
        self._instance: Optional[T] = None
        self._lock = threading.Lock()

    def resolve(self) -> T:
        if not self.singleton:
            return self.factory()
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = self.factory()
        return self._instance

    def get_instance_if_created(self):
        """Return an already-created singleton without triggering its factory."""
        if not self.singleton:
            return None
        with self._lock:
            return self._instance


class ServiceContainer:
    """Lightweight inversion-of-control container."""

    def __init__(self) -> None:
        self._registry: Dict[Type, _Registration] = {}
        self._lock = threading.RLock()

    def register(self, interface, factory, singleton: bool = True):
        with self._lock:
            self._registry[interface] = _Registration(factory, singleton)
        return self

    def register_instance(self, interface, instance):
        reg = _Registration(lambda: instance, singleton=True)
        reg._instance = instance
        with self._lock:
            self._registry[interface] = reg
        return self

    def resolve(self, interface):
        with self._lock:
            reg = self._registry.get(interface)
        if reg is None:
            raise ServiceNotFoundError(interface)
        return reg.resolve()

    def try_resolve(self, interface):
        try:
            return self.resolve(interface)
        except ServiceNotFoundError:
            return None

    def try_get_instance(self, interface):
        """Return an already-created singleton, or None without lazy construction.

        Lifecycle observers use this API so monitoring/registration does not
        accidentally start services that were not part of the selected boot mode.
        """
        with self._lock:
            reg = self._registry.get(interface)
        if reg is None:
            return None
        return reg.get_instance_if_created()

    def is_registered(self, interface) -> bool:
        with self._lock:
            return interface in self._registry

    def registered_types(self) -> list:
        with self._lock:
            return list(self._registry.keys())

    def reset(self) -> None:
        with self._lock:
            self._registry.clear()


_global_container: Optional[ServiceContainer] = None


def set_global_container(container: ServiceContainer) -> None:
    global _global_container
    _global_container = container


def get_container() -> Optional[ServiceContainer]:
    return _global_container


class ServiceNotFoundError(Exception):
    def __init__(self, interface) -> None:
        name = getattr(interface, "__name__", str(interface))
        super().__init__(
            f"No service registered for '{name}'. "
            "Ensure it is registered in dependency_registry.py before use."
        )
        self.interface = interface

