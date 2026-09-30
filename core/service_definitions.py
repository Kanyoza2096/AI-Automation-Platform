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
    from core.unified_bus import UnifiedEventBus, get_unified_bus
    return container.try_resolve(UnifiedEventBus) or get_unified_bus()

define_service("event_bus", _resolve_event_bus, critical=True)


def _resolve_job_service(container):
    from services.job_service import JobService
    return container.try_resolve(JobService)

define_service("job_service", _resolve_job_service, critical=True)


def _resolve_job_stats_service(container):
    from services.job_stats_service import get_job_stats_service
    return get_job_stats_service()

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
    from services.director import director
    return director

define_service("director", _resolve_director, critical=True)


def _resolve_condition_engine(container):
    from services.condition_engine import autonomous_engine
    return autonomous_engine

define_service("condition_engine", _resolve_condition_engine, critical=True)


def _resolve_render_queue(container):
    from core.render_queue import get_render_queue
    return get_render_queue()

define_service("render_queue", _resolve_render_queue, critical=False)


def _resolve_task_queue(container):
    from workers.task_queue import TaskQueue
    return container.try_resolve(TaskQueue)

define_service("task_queue", _resolve_task_queue, critical=False)


def _resolve_meta_engine(container):
    from services.meta_engine import meta_engine
    return meta_engine

define_service("meta_engine", _resolve_meta_engine, critical=True)


def _resolve_autonomous_loop(container):
    from core.autonomous_loop import AutonomousLoop, _autonomous_loop
    return _autonomous_loop or container.try_resolve(AutonomousLoop)

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


def _resolve_topic_selector(container):
    from domain.content.topic_selector import topic_selector
    return topic_selector

define_service("topic_selector", _resolve_topic_selector, critical=False)


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
    failed = []
    
    for name, resolver, critical in SERVICE_DEFINITIONS:
        try:
            service = resolver(container)
            if service is None:
                if critical:
                    logger.warning(f"Critical service '{name}' resolved to None")
                    failed.append(name)
                else:
                    logger.debug(f"Optional service '{name}' resolved to None - skipping")
                continue
            
            registry.register(name, service, auto_register=True)
            registered += 1
            
        except Exception as exc:
            error_msg = str(exc)[:200]
            if critical:
                logger.error(f"Critical service '{name}' failed: {error_msg}")
                failed.append(name)
            else:
                logger.warning(f"Optional service '{name}' failed: {error_msg}")
    
    if failed:
        logger.error(f"Failed to register {len(failed)} services: {failed}")
    
    logger.info(f"✅ Registered {registered}/{len(SERVICE_DEFINITIONS)} services with Meta Engine")
    return registered


__all__ = [
    "SERVICE_DEFINITIONS",
    "define_service",
    "register_all_services",
]
