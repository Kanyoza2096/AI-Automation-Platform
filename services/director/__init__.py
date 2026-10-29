"""
Director package public surface.

Modules are layered to avoid circular imports:
    utils -> models -> stores/analysis -> core -> api.

Explicit re-exports only — no wildcards. Every name here is deliberately
part of the public surface. Internal helpers stay internal.

v5.3: Capability gained target_param; Status gained VERIFIED and
INEFFECTIVE; LearningLoop gained target-scoped learning and startup
replay. See models.py, analysis.py, core.py docstrings for details.
"""

# ─────────────────────────────────────────────────────────────────────
# Core engine
# ─────────────────────────────────────────────────────────────────────
from .core import (
    Director,
    auto_discover_engines,
)

# ─────────────────────────────────────────────────────────────────────
# FastAPI surface and singleton lifecycle
# ─────────────────────────────────────────────────────────────────────
from .api import (
    create_app,
    director,
    init_director,
    get_director,
    shutdown_director,
)

# ─────────────────────────────────────────────────────────────────────
# Data models (dataclasses and enums)
# ─────────────────────────────────────────────────────────────────────
from .models import (
    # Enums
    Authority,
    Briefing,
    Health,
    Risk,
    Sector,
    Status,
    CircuitState,
    # Dataclasses
    Capability,
    Decision,
    DependencyNode,
    Goal,
    LearningSignal,
    Observation,
    ResourceAllocation,
    RootCauseAnalysis,
    StrategicOutlook,
    Task,
    # Infrastructure primitives
    AsyncCircuitBreaker,
    CircuitBreakerOpenError,
    SupabaseErrorClassifier,
)

# ─────────────────────────────────────────────────────────────────────
# Analysis layer (topology, diagnostics, learning)
# ─────────────────────────────────────────────────────────────────────
from .analysis import (
    AdaptiveThresholds,
    CascadingFailurePredictor,
    CorrelatedRecovery,
    FunctionObserver,
    ImpactAssessor,
    LearningLoop,
    Policy,
    RootCauseAnalyzer,
    SystemTopology,
    ENGINE_REGISTRY,
)

# ─────────────────────────────────────────────────────────────────────
# Storage layer
# ─────────────────────────────────────────────────────────────────────
from .stores import (
    AsyncSupabaseStore,
    InMemoryStore,
)

# ─────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────
from .utils import (
    clamp,
    generate_correlation_id,
    iso,
    iso_now,
    now,
    safe,
    sanitize_error_message,
    utcnow,
    _publish_event,
    _publish_event_async,
)

__all__ = [
    # Core
    "Director",
    "auto_discover_engines",
    # API / lifecycle
    "create_app",
    "director",
    "init_director",
    "get_director",
    "shutdown_director",
    # Enums
    "Authority",
    "Briefing",
    "Health",
    "Risk",
    "Sector",
    "Status",
    "CircuitState",
    # Dataclasses
    "Capability",
    "Decision",
    "DependencyNode",
    "Goal",
    "LearningSignal",
    "Observation",
    "ResourceAllocation",
    "RootCauseAnalysis",
    "StrategicOutlook",
    "Task",
    # Infrastructure
    "AsyncCircuitBreaker",
    "CircuitBreakerOpenError",
    "SupabaseErrorClassifier",
    # Analysis
    "AdaptiveThresholds",
    "CascadingFailurePredictor",
    "CorrelatedRecovery",
    "FunctionObserver",
    "ImpactAssessor",
    "LearningLoop",
    "Policy",
    "RootCauseAnalyzer",
    "SystemTopology",
    "ENGINE_REGISTRY",
    # Storage
    "AsyncSupabaseStore",
    "InMemoryStore",
    # Utilities
    "clamp",
    "generate_correlation_id",
    "iso",
    "iso_now",
    "now",
    "safe",
    "sanitize_error_message",
    "utcnow",
    "_publish_event",
    "_publish_event_async",
]
