"""
Kanyoza Systems AI Platform v10 — Lifecycle Management.

Handles application start/stop hooks:
  - Scheduler startup
  - Graceful shutdown (SIGTERM / SIGINT)
  - Resource cleanup

register_lifecycle() is called once during bootstrap, after the Flask
app is created. It must not start any long-running work itself — it
only registers hooks so Flask/Gunicorn can drive the lifecycle.
"""

from __future__ import annotations

import logging
import signal
import sys

from flask import Flask

from service_container import ServiceContainer

logger = logging.getLogger("kanyoza.lifecycle")

_shutdown_requested = False


def register_lifecycle(app: Flask, container: ServiceContainer) -> None:
    """
    Register all application lifecycle callbacks.

    Called once during bootstrap().
    """
    _boot_infrastructure(container)
    _start_schedulers(container)
    _register_signal_handlers(container)

    @app.teardown_appcontext
    def _on_teardown(exc):
        pass

    logger.info("Lifecycle hooks registered.")


# ---------------------------------------------------------------------------
# Infrastructure boot (EventBus, TaskQueue, PluginRegistry, EventRecorder)
# ---------------------------------------------------------------------------


def _boot_infrastructure(container: ServiceContainer) -> None:
    """Eagerly resolve singletons that need to start listening / working."""

    # Event Bus — must be first so subscribers can register
    try:
        from core.event_bus import EventBus
        container.resolve(EventBus)
        logger.info("EventBus ready.")
    except Exception as exc:
        logger.warning("EventBus boot failed: %s", exc)

    # Task Queue — starts worker threads
    try:
        from workers.task_queue import TaskQueue
        container.resolve(TaskQueue)
        logger.info("TaskQueue ready.")
    except Exception as exc:
        logger.warning("TaskQueue boot failed: %s", exc)

    # Plugin Registry — starts all registered plugins
    try:
        from plugins.base import PluginRegistry
        container.resolve(PluginRegistry)
        logger.info("PluginRegistry ready.")
    except Exception as exc:
        logger.warning("PluginRegistry boot failed: %s", exc)

    # EventRecorder — subscribes to bus and persists analytics
    try:
        from domain.analytics.event_recorder import EventRecorder
        container.resolve(EventRecorder)
        logger.info("EventRecorder ready.")
    except Exception as exc:
        logger.warning("EventRecorder boot failed: %s", exc)

    # AI Orchestrator — validate providers are reachable
    try:
        from ai.orchestrator import AIOrchestrator
        orch = container.resolve(AIOrchestrator)
        health = orch.health()
        available = [name for name, h in health.items() if h["available"]]
        logger.info("AIOrchestrator ready. Available providers: %s", available)
    except Exception as exc:
        logger.warning("AIOrchestrator boot failed: %s", exc)

    # BrowserManager (M5) — persistent Playwright page pool for card rendering
    try:
        from services.browser_manager import browser_manager
        browser_manager.start()
        logger.info("BrowserManager ready (page pool started).")
    except Exception as exc:
        logger.warning("BrowserManager boot failed (card rendering will use one-shot fallback): %s", exc)

    # RenderQueue — async card rendering pipeline
    try:
        from core.render_queue import get_render_queue
        get_render_queue()
        logger.info("RenderQueue ready.")
    except Exception as exc:
        logger.warning("RenderQueue boot failed: %s", exc)

    # SocketIO event bridge — subscribe EventBus events → push to /dashboard
    _wire_socket_events(container)


# ---------------------------------------------------------------------------
# EventBus → SocketIO bridge
# ---------------------------------------------------------------------------


def _wire_socket_events(container: ServiceContainer) -> None:
    """Subscribe to every domain event on the EventBus and forward it as a
    SocketIO emission to the /dashboard namespace."""
    try:
        from core.event_bus import EventBus
        from core.events import EventType
        from core.socket_server import emit_to_dashboard, increment

        bus = container.resolve(EventBus)

        def _safe_payload(event) -> dict:
            return {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "source": event.source,
                **event.payload,
            }

        def on_message_received(event):
            try:
                increment("messages_today")
                emit_to_dashboard("new_message", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (new_message): %s", exc)

        def on_post_published(event):
            try:
                increment("posts_today")
                emit_to_dashboard("post_published", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (post_published): %s", exc)

        def on_scan_completed(event):
            try:
                emit_to_dashboard("scan_complete", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (scan_complete): %s", exc)

        def on_provider_failed(event):
            try:
                emit_to_dashboard("provider_failed", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (provider_failed): %s", exc)

        def on_post_generated(event):
            try:
                emit_to_dashboard("post_generated", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (post_generated): %s", exc)

        def on_image_rendered(event):
            try:
                emit_to_dashboard("image_rendered", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (image_rendered): %s", exc)

        def on_worker_error(event):
            try:
                emit_to_dashboard("worker_error", _safe_payload(event))
            except Exception as exc:
                logger.debug("socket bridge error (worker_error): %s", exc)

        bus.subscribe(EventType.MESSAGE_RECEIVED, on_message_received)
        bus.subscribe(EventType.POST_PUBLISHED,   on_post_published)
        bus.subscribe(EventType.SCAN_COMPLETED,   on_scan_completed)
        bus.subscribe(EventType.PROVIDER_FAILED,  on_provider_failed)
        bus.subscribe(EventType.POST_GENERATED,   on_post_generated)
        bus.subscribe(EventType.IMAGE_RENDERED,   on_image_rendered)
        bus.subscribe(EventType.WORKER_ERROR,     on_worker_error)

        logger.info("SocketIO event bridge wired — 7 EventBus subscriptions active.")
    except Exception as exc:
        logger.warning("SocketIO event bridge skipped: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler startup
# ---------------------------------------------------------------------------


def _start_schedulers(container: ServiceContainer) -> None:
    _start_post_scheduler(container)
    _start_brand_schedulers(container)


def _start_post_scheduler(container: ServiceContainer) -> None:
    try:
        from scheduler.post_scheduler import scheduler as post_scheduler

        try:
            from supabase import Client as SupabaseClient
            supabase = container.try_resolve(SupabaseClient)
            if supabase:
                post_scheduler.supabase = supabase
        except Exception:
            pass

        try:
            from workflows.post_workflow import PostPublishWorkflow
            if container.is_registered(PostPublishWorkflow):
                post_scheduler.workflow_factory = lambda: container.resolve(PostPublishWorkflow)
                logger.info("PostPublishWorkflow wired into scheduler.")
            else:
                logger.info("PostPublishWorkflow not registered — scheduler will use legacy path.")
        except Exception as exc:
            logger.warning("Could not wire PostPublishWorkflow into scheduler: %s", exc)

        post_scheduler.start()
        logger.info("Post scheduler started.")
    except Exception as exc:
        logger.warning("Post scheduler could not start: %s", exc)


def _start_brand_schedulers(container: ServiceContainer) -> None:
    """Start per-brand schedulers for multi-tenant posting."""
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = container.resolve(BrandSchedulerManager)
        manager.set_container(container)
        manager.start()
        logger.info("BrandSchedulerManager started.")
    except Exception as exc:
        logger.warning("BrandSchedulerManager could not start: %s", exc)


def _start_guardian_scheduler(container: ServiceContainer) -> None:
    """M7: Start Code Guardian v10 using the DI-registered GuardianService."""
    from config.settings import settings

    if not settings.guardian_configured:
        logger.info("Code Guardian disabled (not configured).")
        return

    try:
        from services.guardian_service import GuardianService
        from scheduler.scan_scheduler import ScanScheduler

        guardian = container.resolve(GuardianService)

        try:
            from routes.webhook import set_guardian_runner
            set_guardian_runner(guardian.scan_all)
            logger.info("Code Guardian: wired to /scan slash command.")
        except Exception as exc:
            logger.debug("Could not wire guardian to webhook slash handler: %s", exc)

        scan_scheduler = ScanScheduler(guardian.scan_all, settings.guardian_scan_hours)
        scan_scheduler.start()
        logger.info(
            "Code Guardian v10 started (every %dh, GitHub user=%s).",
            settings.guardian_scan_hours,
            settings.github_username,
        )

    except Exception as exc:
        logger.warning("Code Guardian could not start: %s", exc)


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------


def _register_signal_handlers(container: ServiceContainer) -> None:
    def _shutdown(signum, frame):
        global _shutdown_requested
        if _shutdown_requested:
            return
        _shutdown_requested = True
        logger.info("Shutdown signal received (%s) — stopping services...", signum)
        _graceful_shutdown(container)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def _graceful_shutdown(container: ServiceContainer) -> None:
    """Stop all running services cleanly."""

    try:
        from plugins.base import PluginRegistry
        registry = container.try_resolve(PluginRegistry)
        if registry:
            registry.stop_all()
            logger.info("PluginRegistry stopped.")
    except Exception as exc:
        logger.warning("Error stopping PluginRegistry: %s", exc)

    try:
        from workers.task_queue import TaskQueue
        tq = container.try_resolve(TaskQueue)
        if tq:
            tq.stop(timeout=10)
            logger.info("TaskQueue stopped.")
    except Exception as exc:
        logger.warning("Error stopping TaskQueue: %s", exc)

    try:
        from scheduler.post_scheduler import scheduler as post_scheduler
        post_scheduler.stop(timeout=30)
        logger.info("Post scheduler stopped.")
    except Exception as exc:
        logger.warning("Error stopping post scheduler: %s", exc)

    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        brand_manager = container.try_resolve(BrandSchedulerManager)
        if brand_manager:
            brand_manager.stop()
            logger.info("BrandSchedulerManager stopped.")
    except Exception as exc:
        logger.warning("Error stopping BrandSchedulerManager: %s", exc)

    try:
        from services.browser_manager import browser_manager
        browser_manager.stop()
        logger.info("BrowserManager stopped.")
    except Exception as exc:
        logger.warning("Error stopping BrowserManager: %s", exc)

    # RenderQueue — stop async rendering workers
    try:
        from core.render_queue import get_render_queue
        rq = get_render_queue()
        rq.stop()
        logger.info("RenderQueue stopped.")
    except Exception as exc:
        logger.warning("Error stopping RenderQueue: %s", exc)

    _service_singletons = [
        ("services.gemini", "gemini"),
        ("services.facebook", "facebook"),
        ("services.news_scraper", "news_scraper"),
    ]
    for svc_name, attr in _service_singletons:
        try:
            svc_module = __import__(svc_name, fromlist=[""])
            obj = getattr(svc_module, attr, None)
            if obj and hasattr(obj, "close"):
                obj.close()
        except Exception:
            pass

    logger.info("Graceful shutdown complete.")
