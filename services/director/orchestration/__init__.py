"""Opt-in native engine orchestration primitives for Director.

Importing this package does not initialize engines, start workers, or enable
mission execution. Runtime registration is explicit and feature-gated.
"""
from .contracts import (
    AdapterInvocationTimeout,
    AdapterRegistry,
    EngineContribution,
    MissionTaskContext,
    NativeEngineAdapter,
)
from .native_adapters import ContentIntelligenceMissionAdapter

__all__ = [
    "AdapterInvocationTimeout",
    "AdapterRegistry",
    "EngineContribution",
    "MissionTaskContext",
    "NativeEngineAdapter",
    "ContentIntelligenceMissionAdapter",
]
