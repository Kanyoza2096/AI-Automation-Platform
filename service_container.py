"""
Kanyoza Systems AI Platform v10 — Service Container (Dependency Injection).

Design principles:
  - Services are registered once and resolved on-demand.
  - Singletons are created lazily on first resolve and cached.
  - Thread-safe — safe to resolve services from concurrent request threads.
  - No circular imports — this module imports nothing from the application layer.
  - Generic resolve() preserves type information for static analysis.

Usage:
    container = ServiceContainer()
    container.register(AIService, lambda: GeminiAIService(config))
    ai = container.resolve(AIService)   # type: AIService
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Generic, Optional, Type, TypeVar

T = TypeVar("T")


class _Registration(Generic[T]):
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
        # Double-checked locking for thread-safe singleton creation
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = self.factory()
        return self._instance  # type: ignore[return-value]


class ServiceContainer:
    """
    Lightweight inversion-of-control container.

    - register(interface, factory, singleton=True)
        Bind an interface (class or abstract base) to a factory callable.
        The factory receives no arguments; capture dependencies via closure.

    - register_instance(interface, instance)
        Register an already-constructed instance as a singleton.

    - resolve(interface) -> T
        Return the service bound to *interface*.
        Raises ServiceNotFoundError when no binding exists.

    - is_registered(interface) -> bool
        Check registration without creating an instance.
    """

    def __init__(self) -> None:
        self._registry: Dict[Type, _Registration] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        interface: Type[T],
        factory: Callable[[], T],
        singleton: bool = True,
    ) -> "ServiceContainer":
        """
        Bind *interface* to *factory*.

        Parameters
        ----------
        interface : Type[T]
            The type key used for resolution (typically an ABC or Protocol).
        factory : Callable[[], T]
            Zero-argument callable that constructs the service.
        singleton : bool
            When True (default) the instance is created once and cached.
            When False a new instance is created on every resolve().

        Returns self for fluent chaining.
        """
        with self._lock:
            self._registry[interface] = _Registration(factory, singleton)
        return self

    def register_instance(self, interface: Type[T], instance: T) -> "ServiceContainer":
        """
        Register an already-constructed *instance* as a singleton.
        Equivalent to register(interface, lambda: instance, singleton=True).
        """
        reg: _Registration[T] = _Registration(lambda: instance, singleton=True)
        reg._instance = instance  # Skip factory on first resolve
        with self._lock:
            self._registry[interface] = reg
        return self

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, interface: Type[T]) -> T:
        """
        Return the service bound to *interface*.

        Raises
        ------
        ServiceNotFoundError
            When no binding exists for *interface*.
        """
        with self._lock:
            reg = self._registry.get(interface)
        if reg is None:
            raise ServiceNotFoundError(interface)
        return reg.resolve()

    def try_resolve(self, interface: Type[T]) -> Optional[T]:
        """
        Return the service or None if no binding exists.
        Never raises.
        """
        try:
            return self.resolve(interface)
        except ServiceNotFoundError:
            return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def is_registered(self, interface: Type) -> bool:
        """Return True when *interface* has a registered binding."""
        with self._lock:
            return interface in self._registry

    def registered_types(self) -> list:
        """Return list of all registered interface types."""
        with self._lock:
            return list(self._registry.keys())

    def reset(self) -> None:
        """
        Clear all registrations and cached instances.
        Intended for use in tests only — do not call in production code.
        """
        with self._lock:
            self._registry.clear()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ServiceNotFoundError(Exception):
    """Raised when resolve() is called for an unregistered interface."""

    def __init__(self, interface: Type) -> None:
        super().__init__(
            f"No service registered for '{interface.__name__}'. "
            "Ensure it is registered in dependency_registry.py before use."
        )
        self.interface = interface
