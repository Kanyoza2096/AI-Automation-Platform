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
from .condition_engine_adapter import ConditionEngineMissionAdapter
from .meta_engine_adapter import MetaEngineMissionAdapter
from .registry_factory import build_default_adapter_registry
from .runtime import MissionOrchestrationRuntime, MissionRuntimeNotFound, MissionTaskNotReady
from .store import MissionEventWrite, MissionRuntimeStore, MissionRuntimeStoreError
from .state_machine import (
    MissionEventCommand,
    MissionRuntimeConflict,
    MissionRuntimeSnapshot,
    MissionStateMachine,
    MissionTransition,
    MissionTransitionRejected,
)

__all__ = [
    "AdapterInvocationTimeout",
    "AdapterRegistry",
    "EngineContribution",
    "MissionTaskContext",
    "NativeEngineAdapter",
    "ContentIntelligenceMissionAdapter",
    "ConditionEngineMissionAdapter",
    "MetaEngineMissionAdapter",
    "build_default_adapter_registry",
    "MissionOrchestrationRuntime",
    "MissionRuntimeNotFound",
    "MissionTaskNotReady",
    "MissionEventWrite",
    "MissionRuntimeStore",
    "MissionRuntimeStoreError",
    "MissionEventCommand",
    "MissionRuntimeConflict",
    "MissionRuntimeSnapshot",
    "MissionStateMachine",
    "MissionTransition",
    "MissionTransitionRejected",
]
