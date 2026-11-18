"""Explicit production adapter registry assembly for Director missions."""
from __future__ import annotations

from typing import Any

from .contracts import AdapterRegistry
from .native_adapters import ContentIntelligenceMissionAdapter
from .condition_engine_adapter import ConditionEngineMissionAdapter


def build_default_adapter_registry(
    *,
    director_store: Any,
    condition_engine: Any | None = None,
) -> AdapterRegistry:
    """Build only adapters whose workspace boundaries are explicitly wired."""
    verifier = getattr(director_store, "brand_belongs_to_workspace", None)
    if not callable(verifier):
        raise ValueError("director_store_brand_ownership_verifier_required")

    registry = AdapterRegistry()
    registry.register(ContentIntelligenceMissionAdapter())
    registry.register(
        ConditionEngineMissionAdapter(
            engine=condition_engine,
            brand_ownership_verifier=verifier,
        )
    )
    return registry
