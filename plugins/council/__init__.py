"""DEPRECATED — use council.*"""
from __future__ import annotations
import warnings
warnings.warn("plugins.council deprecated", DeprecationWarning, stacklevel=2)
try:
    from council.orchestration.council_orchestrator import CouncilOrchestrator
except ImportError:
    CouncilOrchestrator = None  # type: ignore
__all__ = ["CouncilOrchestrator"]
