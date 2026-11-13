"""
Service definitions for automatic registration.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("kanyoza.service_definitions")


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

# Each entry: (service_name, resolver_function, is_critical)
# resolver_function: takes container, returns service instance
SERVICE_DEFINITIONS: List[Tuple[str, Callable, bool]] = []


def define_service(name: str, resolver: Callable, critical: bool = True):
    """Define a service for automatic registration."""
    SERVICE_DEFINITIONS.append((name, resolver, critical))
    logger.debug(f"Service '{name}' defined (critical={critical})")


# ── Infrastructure Services ──────────────────────────────────────────────

def _resolve_event_bus(container):
    from core.unified_bus import UnifiedEventBus
    return _resolve_existing(container, UnifiedEventBus)

define_service("event_bus", _resolve_event_bus, critical=True)


def _resolve_existing(container, service_type):
    """Resolve only a live singleton created by its owning boot step."""
    if container is None:
        return None
    getter = getattr(container, "try_get_instance", None)
    if callable(getter):
        return getter(service_type)
    # Compatibility for non-production test/adaptor containers: require an
    # explicit instance-only API rather than falling back to lazy try_resolve.
    return None


def _resolve_job_service(container):
    from services.job_service import JobService
    return _resolve_existing(container, JobService)

define_service("job_service", _resolve_job_service, critical=True)


def _resolve_job_stats_service(container):
    # Read the owner-initialized singleton only; never construct it during
    # registry collection in a service mode that skipped its boot step.
    from services.job_stats_service import job_stats_service
    return job_stats_service

define_service("job_stats_service", _resolve_job_stats_service, critical=False)


def _resolve_prompt_builder(container):
    from services.prompt_builder import prompt_builder
    return prompt_builder

define_service("prompt_builder", _resolve_prompt_builder, critical=True)


def _resolve_content_engine(container):
    from services.content_engine import content_engine
    return content_engine

define_service("content_engine", _resolve_content_engine, critical=True)


def _resolve_director(container):
    # Import from the owning API module at resolution time. The package
    # re-export in services.director is a snapshot of the initial None value
    # and does not update when init_director assigns api.director.
    from services.director.api import director
    return director

define_service("director", _resolve_director, critical=True)


def _resolve_condition_engine(container):
    from services.condition_engine import autonomous_engine
    return autonomous_engine

define_service("condition_engine", _resolve_condition_engine, critical=True)


def _resolve_render_queue(container):
    from core.render_queue import get_render_queue_if_initialized
    return get_render_queue_if_initialized()

define_service("render_queue", _resolve_render_queue, critical=False)


def _resolve_task_queue(container):
    from workers.task_queue import TaskQueue
    return _resolve_existing(container, TaskQueue)

define_service("task_queue", _resolve_task_queue, critical=False)


def _resolve_ai_orchestrator(container):
    from ai.orchestrator import AIOrchestrator
    return _resolve_existing(container, AIOrchestrator)

define_service("ai_orchestrator", _resolve_ai_orchestrator, critical=False)


def _resolve_plugin_registry(container):
    from plugins.base import PluginRegistry
    return _resolve_existing(container, PluginRegistry)

define_service("plugin_registry", _resolve_plugin_registry, critical=False)


def _resolve_scheduler(container):
    from scheduler.brand_scheduler import BrandSchedulerManager
    return _resolve_existing(container, BrandSchedulerManager)

define_service("scheduler", _resolve_scheduler, critical=False)


def _resolve_event_recorder(container):
    from domain.analytics.event_recorder import EventRecorder
    return _resolve_existing(container, EventRecorder)

define_service("event_recorder", _resolve_event_recorder, critical=False)


def _resolve_health_monitor(container):
    from services.health_monitor_service import HealthMonitorService
    return _resolve_existing(container, HealthMonitorService)

define_service("health_monitor", _resolve_health_monitor, critical=False)


def _resolve_email_service(container):
    from services.email_service import email_service
    return email_service

define_service("email_service", _resolve_email_service, critical=False)


def _resolve_engagement_tracker(container):
    from services.engagement_tracker import engagement_tracker
    return engagement_tracker

define_service("engagement_tracker", _resolve_engagement_tracker, critical=False)


def _resolve_meta_engine(container):
    from services.meta_engine import meta_engine
    return meta_engine

define_service("meta_engine", _resolve_meta_engine, critical=True)


def _resolve_autonomous_loop(container):
    from core.autonomous_loop import AutonomousLoop, _autonomous_loop
    return _autonomous_loop or _resolve_existing(container, AutonomousLoop)

define_service("autonomous_loop", _resolve_autonomous_loop, critical=False)


def _resolve_autonomous_content(container):
    from core.autonomous_content import _autonomous_content
    return _autonomous_content

define_service("autonomous_content", _resolve_autonomous_content, critical=False)


def _resolve_card_renderer(container):
    from services.rendering.engine import card_renderer
    return card_renderer

define_service("card_renderer", _resolve_card_renderer, critical=False)


def _resolve_image_engine(container):
    from services.image_engine import image_engine
    return image_engine

define_service("image_engine", _resolve_image_engine, critical=False)



# ═══════════════════════════════════════════════════════════════════════════
# REGISTRATION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def register_all_services(container, meta_engine) -> int:
    """
    Register all defined services with Meta Engine.
    
    Returns:
        Number of services successfully registered
    """
    from core.service_registry import get_service_registry
    
    registry = get_service_registry()
    registry.set_container(container)
    registry.set_meta_engine(meta_engine)
    
    registered = 0
    report = {"registered": [], "skipped": [], "failed": [], "meta_registration_failures": []}

    for name, resolver, critical in SERVICE_DEFINITIONS:
        try:
            service = resolver(container)
            if service is None:
                item = {"service": name, "reason": "resolved_none", "critical": critical}
                if critical:
                    report["failed"].append(item)
                    logger.warning("Critical service '%s' resolved to None", name)
                else:
                    report["skipped"].append(item)
                    logger.debug("Optional service '%s' resolved to None; skipping", name)
                continue

            registry.register(name, service, auto_register=True)
            report["registered"].append(name)
            registered += 1

        except Exception as exc:
            item = {"service": name, "reason": type(exc).__name__, "critical": critical}
            if critical:
                report["failed"].append(item)
                logger.error("Critical service '%s' failed: %s", name, type(exc).__name__)
            else:
                report["skipped"].append(item)
                logger.warning("Optional service '%s' failed: %s", name, type(exc).__name__)

    report["meta_registration_failures"] = [
        {"service": name, "reason": reason}
        for name, reason in sorted(registry._meta_registration_failures.items())
    ]
    registry._last_registration_report = report

    if report["failed"]:
        logger.error("Failed to register %d critical services: %s", len(report["failed"]), [x["service"] for x in report["failed"]])

    logger.info("Registered %d/%d services with Meta Engine", registered, len(SERVICE_DEFINITIONS))
    return registered


__all__ = [
    "SERVICE_DEFINITIONS",
    "define_service",
    "register_all_services",
]
