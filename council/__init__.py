# council/__init__.py — MINIMAL AND CLEAN

"""
Kanyoza Democracy Engine v1.0
==============================

Complete AI governance with:
- Adaptive risk routing
- Tiered deliberation (Fast/Standard/Deep)
- 12 voting personas
- Reliability tracking
- Outcome memory
- Fallback strategies
- Action/autonomy/veto/safety policies
"""

from council.orchestration.council_orchestrator import CouncilOrchestrator

__all__ = ["CouncilOrchestrator"]