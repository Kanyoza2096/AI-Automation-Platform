"""
Centralized Service Registry — Single source of truth for all services.

All services that need to be monitored by Meta Engine register here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("kanyoza.service_registry")


@runtime_checkable
class RegistrableService(Protocol):
    """Protocol for services that can be registered with Meta Engine."""
    
    @property
    def service_name(self) -> str: ...
    def health_check(self) -> dict: ...
    def get_status(self) -> dict: ...


class ServiceRegistry:
    """
    Centralized registry for all platform services.
    
    Features:
      - Auto-registration with Meta Engine
      - Health check aggregation
      - Service discovery
      - Dependency ordering
    """
    
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._meta_engine = None
        self._container = None
        self._registered = False
    
    def set_container(self, container) -> None:
        """Set the DI container for service resolution."""
        self._container = container
    
    def set_meta_engine(self, meta_engine) -> None:
        """Set the Meta Engine instance."""
        self._meta_engine = meta_engine
    
    def register(self, name: str, service: Any, auto_register: bool = True) -> None:
        """
        Register a service with the registry.
        
        Args:
            name: Unique service name
            service: Service instance
            auto_register: If True, automatically register with Meta Engine
        """
        if name in self._services:
            logger.warning(f"Service '{name}' already registered, overwriting")
        
        self._services[name] = service
        
        if auto_register and self._meta_engine:
            try:
                self._meta_engine.register(name, service)
                logger.info(f"✅ Service '{name}' registered with Meta Engine")
            except Exception as exc:
                logger.warning(f"Failed to register '{name}' with Meta Engine: {exc}")
        
        logger.info(f"📝 Service '{name}' registered in registry")
    
    def get(self, name: str) -> Optional[Any]:
        """Get a service by name."""
        return self._services.get(name)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all registered services."""
        return self._services.copy()
    
    def get_names(self) -> List[str]:
        """Get all service names."""
        return list(self._services.keys())
    
    def register_all_with_meta(self) -> int:
        """Register all services with Meta Engine."""
        if not self._meta_engine:
            logger.warning("Meta Engine not set - cannot register services")
            return 0
        
        registered = 0
        for name, service in self._services.items():
            try:
                self._meta_engine.register(name, service)
                registered += 1
                logger.debug(f"✅ Registered '{name}' with Meta Engine")
            except Exception as exc:
                logger.warning(f"Failed to register '{name}': {exc}")
        
        self._registered = True
        logger.info(f"✅ Registered {registered} services with Meta Engine")
        return registered
    
    def health_check_all(self) -> Dict[str, Any]:
        """Run health check on all registered services."""
        results = {}
        for name, service in self._services.items():
            try:
                if hasattr(service, "health_check"):
                    result = service.health_check()
                    if callable(result):
                        result = result()
                    results[name] = result
                elif hasattr(service, "get_status"):
                    result = service.get_status()
                    if callable(result):
                        result = result()
                    results[name] = result
                else:
                    results[name] = {"status": "unknown", "note": "no health_check"}
            except Exception as exc:
                results[name] = {"status": "error", "error": str(exc)[:200]}
        
        return results
    
    def get_service_status(self) -> Dict[str, str]:
        """Get simplified status of all services."""
        statuses = {}
        for name, service in self._services.items():
            try:
                if hasattr(service, "health_check"):
                    result = service.health_check()
                    if isinstance(result, dict):
                        statuses[name] = result.get("status", "unknown")
                    else:
                        statuses[name] = "unknown"
                else:
                    statuses[name] = "unknown"
            except Exception:
                statuses[name] = "error"
        return statuses


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_service_registry: Optional[ServiceRegistry] = None


def get_service_registry() -> ServiceRegistry:
    """Get the singleton service registry."""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry()
    return _service_registry


def init_service_registry(container=None, meta_engine=None) -> ServiceRegistry:
    """Initialize the service registry with dependencies."""
    registry = get_service_registry()
    if container:
        registry.set_container(container)
    if meta_engine:
        registry.set_meta_engine(meta_engine)
    return registry


__all__ = [
    "ServiceRegistry",
    "get_service_registry",
    "init_service_registry",
    "RegistrableService",
]
