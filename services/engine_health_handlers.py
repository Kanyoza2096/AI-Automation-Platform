"""
KANYOZA ENGINE HEALTH HANDLERS — COMPLETE
============================================

Provides health check functions for ALL engines that lack native
health_check() or get_status() methods.

COVERS ALL ENGINES in MetaEngine's registry.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("kanyoza.health_handlers")


# ═══════════════════════════════════════════════════════════════════════════
# AI ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def ai_orchestrator_health(engine: Any) -> Dict[str, Any]:
    """Health check for AIOrchestrator."""
    try:
        if hasattr(engine, "health"):
            result = engine.health()
            if isinstance(result, dict):
                available = sum(
                    1 for h in result.values()
                    if isinstance(h, dict) and h.get("available")
                )
                return {
                    "status": "healthy" if available > 0 else "degraded",
                    "available_providers": available,
                    "total_providers": len(result),
                }
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if engine is not None:
            return {"status": "healthy", "message": "AIOrchestrator available"}
        return {"status": "offline", "message": "AIOrchestrator not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# CONTENT ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def content_engine_health(engine: Any) -> Dict[str, Any]:
    """Health check for ContentEngine."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        if hasattr(engine, "_container") and engine._container is not None:
            return {"status": "healthy", "message": "ContentEngine container wired"}
        if hasattr(engine, "_redis") and engine._redis is not None:
            return {"status": "healthy", "message": "ContentEngine Redis wired"}
        if engine is not None:
            return {"status": "healthy", "message": "ContentEngine available"}
        return {"status": "offline", "message": "ContentEngine not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# CARD RENDERER
# ═══════════════════════════════════════════════════════════════════════════

def card_renderer_health(engine: Any) -> Dict[str, Any]:
    """Health check for CardRenderer."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        if engine is not None:
            return {"status": "healthy", "message": "CardRenderer available"}
        return {"status": "offline", "message": "CardRenderer not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def image_engine_health(engine: Any) -> Dict[str, Any]:
    """Health check for ImageEngine."""
    try:
        if engine is not None and callable(engine):
            return {"status": "healthy", "message": "ImageEngine callable available"}
        if engine is not None:
            return {"status": "healthy", "message": "ImageEngine available"}
        return {"status": "offline", "message": "ImageEngine not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# TOPIC SELECTOR
# ═══════════════════════════════════════════════════════════════════════════

def topic_selector_health(engine: Any) -> Dict[str, Any]:
    """Health check for TopicSelector."""
    try:
        if engine is not None and not isinstance(engine, type):
            return {"status": "healthy", "message": "TopicSelector instance available"}
        if engine is not None and isinstance(engine, type):
            return {"status": "healthy", "message": "TopicSelector class available"}
        return {"status": "offline", "message": "TopicSelector not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def prompt_builder_health(engine: Any) -> Dict[str, Any]:
    """Health check for PromptBuilder."""
    try:
        if engine is not None:
            return {"status": "healthy", "message": "PromptBuilder available"}
        return {"status": "offline", "message": "PromptBuilder not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH MONITOR (NEW — was missing)
# ═══════════════════════════════════════════════════════════════════════════

def health_monitor_health(engine: Any) -> Dict[str, Any]:
    """Health check for HealthMonitorService."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        if hasattr(engine, "running") and engine.running:
            return {"status": "healthy", "message": "HealthMonitor running"}
        if engine is not None:
            return {"status": "healthy", "message": "HealthMonitor available"}
        return {"status": "offline", "message": "HealthMonitor not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# PLUGIN REGISTRY (NEW — was missing)
# ═══════════════════════════════════════════════════════════════════════════

def plugin_registry_health(engine: Any) -> Dict[str, Any]:
    """Health check for PluginRegistry."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        # Check if registry has plugins loaded
        if hasattr(engine, "plugins") or hasattr(engine, "_plugins"):
            plugins = getattr(engine, "plugins", None) or getattr(engine, "_plugins", {})
            return {
                "status": "healthy",
                "plugins_registered": len(plugins) if hasattr(plugins, "__len__") else 0,
            }
        if engine is not None:
            return {"status": "healthy", "message": "PluginRegistry available"}
        return {"status": "offline", "message": "PluginRegistry not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# RENDER QUEUE (NEW — was missing)
# ═══════════════════════════════════════════════════════════════════════════

def render_queue_health(engine: Any) -> Dict[str, Any]:
    """Health check for RenderQueue."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        # Check queue size
        if hasattr(engine, "_pending"):
            pending = len(engine._pending)
            return {
                "status": "healthy" if pending < 100 else "degraded",
                "pending_tasks": pending,
            }
        if engine is not None:
            return {"status": "healthy", "message": "RenderQueue available"}
        return {"status": "offline", "message": "RenderQueue not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# MONITOR PLUGIN (NEW — was missing)
# ═══════════════════════════════════════════════════════════════════════════

def monitor_plugin_health(engine: Any) -> Dict[str, Any]:
    """Health check for MonitorPlugin."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        if hasattr(engine, "supabase") and engine.supabase is not None:
            return {"status": "healthy", "message": "MonitorPlugin Supabase wired"}
        if engine is not None:
            return {"status": "healthy", "message": "MonitorPlugin available"}
        return {"status": "offline", "message": "MonitorPlugin not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP PLUGIN (NEW — was missing)
# ═══════════════════════════════════════════════════════════════════════════

def backup_plugin_health(engine: Any) -> Dict[str, Any]:
    """Health check for BackupPlugin."""
    try:
        if hasattr(engine, "get_status"):
            return engine.get_status()
        if hasattr(engine, "health_check"):
            return engine.health_check()
        if hasattr(engine, "supabase") and engine.supabase is not None:
            return {"status": "healthy", "message": "BackupPlugin Supabase wired"}
        if engine is not None:
            return {"status": "healthy", "message": "BackupPlugin available"}
        return {"status": "offline", "message": "BackupPlugin not available"}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# TASK QUEUE
# ═══════════════════════════════════════════════════════════════════════════

def task_queue_health(engine: Any) -> Dict[str, Any]:
    """Report queue lifecycle and backpressure without treating idle as failure."""
    try:
        if engine is None:
            return {"status": "offline", "reason": "TaskQueue instance unavailable"}
        stats = engine.stats() if callable(getattr(engine, "stats", None)) else {}
        if not isinstance(stats, dict):
            return {"status": "unknown", "reason": "TaskQueue stats unavailable"}
        if stats.get("running") is not True:
            return {"status": "unknown", "reason": "TaskQueue is not running in this process", "stats": stats}
        queued = int(stats.get("queued", 0) or 0)
        maxsize = int(stats.get("maxsize", 0) or 0)
        if maxsize > 0 and queued >= maxsize:
            return {"status": "degraded", "reason": "TaskQueue is at capacity", "stats": stats}
        return {"status": "healthy", "stats": stats}
    except Exception as exc:
        return {"status": "degraded", "message": str(exc)[:200]}


# ═══════════════════════════════════════════════════════════════════════════
# COMPLETE REGISTRY — ALL ENGINES
# ═══════════════════════════════════════════════════════════════════════════

HEALTH_HANDLERS = {
    # Engines WITHOUT native health interfaces
    "ai_orchestrator": ai_orchestrator_health,
    "content_engine": content_engine_health,
    "card_renderer": card_renderer_health,
    "image_engine": image_engine_health,
    "topic_selector": topic_selector_health,
    "prompt_builder": prompt_builder_health,
    "health_monitor": health_monitor_health,       # NEW
    "plugin_registry": plugin_registry_health,     # NEW
    "render_queue": render_queue_health,           # NEW
    "monitor_plugin": monitor_plugin_health,       # NEW
    "backup_plugin": backup_plugin_health,         # NEW
}


# Engines that have NATIVE health interfaces (no handler needed)
NATIVE_HEALTH_ENGINES = {
    "condition_engine",      # Has health_check()
    "email_service",         # Has get_stats()
    "engagement_tracker",    # Has get_status()
    "guardian",              # Has health_check()
    "meta_engine",           # Has get_status()
    "meta_director",         # Has get_status()
    "scheduler",             # Has get_status()
    "job_service",           # Has get_stats()
    "event_bus",             # Has get_stats()
    "growthbridge_plugin",   # Has health endpoint
}


def register_all_health_handlers(meta_engine) -> Dict[str, Any]:
    """Register ALL health handlers with MetaEngine.

    Returns summary of what was registered.
    """
    registered = []
    failed = []

    for engine_name, handler in HEALTH_HANDLERS.items():
        try:
            meta_engine.register_health_handler(engine_name, handler)
            registered.append(engine_name)
        except Exception as exc:
            logger.warning(
                "Failed to register handler for %s: %s",
                engine_name, exc,
            )
            failed.append(engine_name)

    logger.info(
        "Registered %d health handlers: %s",
        len(registered),
        ", ".join(registered),
    )

    return {
        "registered": registered,
        "failed": failed,
        "native_health": sorted(NATIVE_HEALTH_ENGINES),
        "total_covered": len(registered) + len(NATIVE_HEALTH_ENGINES),
    }
