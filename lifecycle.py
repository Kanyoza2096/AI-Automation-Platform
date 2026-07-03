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

    # SocketIO event bridge — subscribe EventBus events → push to /dashboard
    # Must use the DI-resolved bus (same instance used by workflows/services)
    _wire_socket_events(container)


# ---------------------------------------------------------------------------
# EventBus → SocketIO bridge
# ---------------------------------------------------------------------------


def _wire_socket_events(container: ServiceContainer) -> None:
    """
    Subscribe to every domain event on the EventBus and forward it as a
    SocketIO emission to the /dashboard namespace.

    IMPORTANT: Uses the DI-resolved EventBus (same instance used by workflows,
    the EventRecorder, and all services) — NOT the get_event_bus() module
    singleton which is a separate instance and would miss all DI-published events.
    """
    try:
        from core.event_bus import EventBus
        from core.events import EventType
        from core.socket_server import emit_to_dashboard, increment

        # Resolve the same bus the rest of the app uses
        bus = container.resolve(EventBus)

        def _safe_payload(event) -> dict:
            """Convert a DomainEvent to a JSON-safe dict."""
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

        logger.info(
            "SocketIO event bridge wired — 7 EventBus subscriptions active."
        )
    except Exception as exc:
        logger.warning("SocketIO event bridge skipped: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler startup
# ---------------------------------------------------------------------------


def _start_schedulers(container: ServiceContainer) -> None:
    _start_v9_post_scheduler(container)
    _start_v9_guardian_scheduler(container)


def _start_v9_post_scheduler(container: ServiceContainer) -> None:
    try:
        from scheduler.post_scheduler import scheduler as post_scheduler

        try:
            from supabase import Client as SupabaseClient  # type: ignore[import-untyped]
            supabase = container.try_resolve(SupabaseClient)
            if supabase:
                post_scheduler.supabase = supabase
        except Exception:
            pass

        post_scheduler.start()
        logger.info("v9 post scheduler started.")
    except Exception as exc:
        logger.warning("v9 post scheduler could not start: %s", exc)


def _start_v9_guardian_scheduler(container: ServiceContainer) -> None:
    from config.settings import settings

    if not settings.guardian_configured:
        logger.info("Code Guardian disabled (not configured).")
        return

    try:
        import tempfile
        import shutil
        from pathlib import Path
        from datetime import datetime, timezone

        from guardian.github_client import GitHubClient
        from guardian.scanner import CodeGuardianScanner
        from guardian.auto_fix import auto_fix
        from services.facebook import facebook as fb_service
        from scheduler.scan_scheduler import ScanScheduler

        github = GitHubClient(settings.github_username, settings.github_token)
        scanner = CodeGuardianScanner()

        def run_scan():
            logger.info("Code Guardian: starting scan...")
            temp_dir = tempfile.mkdtemp(prefix="guardian_")
            try:
                repos = github.get_all_repos(exclude=settings.guardian_exclude_repos)
                if not repos:
                    return
                repo_paths = []
                for repo in repos:
                    try:
                        repo_paths.append({
                            "name": repo["name"],
                            "local_path": github.clone_or_pull(repo, temp_dir),
                        })
                    except Exception as e:
                        logger.error("Failed to clone %s: %s", repo["name"], e)

                exclude = {"venv", ".venv", "env", "__pycache__", "node_modules", ".git", "dist", "build"}
                total_files = sum(
                    len([f for f in Path(rp["local_path"]).rglob("*")
                         if f.is_file() and not any(e in f.parts for e in exclude)])
                    for rp in repo_paths
                )
                issues = scanner.scan_repos(repo_paths)
                report = scanner.build_report(issues, len(repos), total_files)

                if auto_fix.is_configured and report["total_issues"] > 0:
                    for repo in repos:
                        repo_full = f"{settings.github_username}/{repo['name']}"
                        repo_issues = [i for i in report["issues"] if i.get("repo") == repo["name"]]
                        if repo_issues:
                            try:
                                fix_results = auto_fix.auto_fix_all(repo_full, repo_issues)
                                if fix_results["fixed"] > 0:
                                    logger.info("Auto-fixed %d issues in %s", fix_results["fixed"], repo["name"])
                            except Exception as e:
                                logger.error("Auto-fix error: %s", e)

                if settings.owner_psid:
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if report["total_issues"] == 0:
                        msg = f"🛡️ Code Guardian\n\n📅 {now}\n📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n✅ ALL CLEAR"
                    else:
                        msg = (
                            f"🛡️ Code Guardian\n\n📅 {now}\n"
                            f"📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n"
                            f"🔴 {report['critical']}  🟠 {report['high']}  🟡 {report['medium']}\n"
                            f"⚠️ {report['total_issues']} issues"
                        )
                    fb_service.send_message(settings.owner_psid, msg)

                # Emit event to bus
                try:
                    from core.event_bus import get_event_bus
                    from core.events import ScanCompletedEvent
                    get_event_bus().publish(ScanCompletedEvent(
                        repo=settings.github_username,
                        issues_found=report["total_issues"],
                        critical=report.get("critical", 0),
                    ))
                except Exception:
                    pass

                logger.info("Guardian scan complete: %d issues", report["total_issues"])
            except Exception as e:
                logger.error("Guardian scan failed: %s", e)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        guardian_scheduler = ScanScheduler(run_scan, settings.guardian_scan_hours)
        guardian_scheduler.start()
        logger.info("Code Guardian started (every %dh).", settings.guardian_scan_hours)

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

    # Stop plugin registry
    try:
        from plugins.base import PluginRegistry
        registry = container.try_resolve(PluginRegistry)
        if registry:
            registry.stop_all()
            logger.info("PluginRegistry stopped.")
    except Exception as exc:
        logger.warning("Error stopping PluginRegistry: %s", exc)

    # Stop task queue
    try:
        from workers.task_queue import TaskQueue
        tq = container.try_resolve(TaskQueue)
        if tq:
            tq.stop(timeout=10)
            logger.info("TaskQueue stopped.")
    except Exception as exc:
        logger.warning("Error stopping TaskQueue: %s", exc)

    # v9 post scheduler
    try:
        from scheduler.post_scheduler import scheduler as post_scheduler
        post_scheduler.stop(timeout=30)
        logger.info("Post scheduler stopped.")
    except Exception as exc:
        logger.warning("Error stopping post scheduler: %s", exc)

    # BrowserManager (M5) — close the persistent page pool
    try:
        from services.browser_manager import browser_manager
        browser_manager.stop()
        logger.info("BrowserManager stopped.")
    except Exception as exc:
        logger.warning("Error stopping BrowserManager: %s", exc)

    # v9 service singletons with .close()
    _v9_singletons = [
        ("services.gemini", "gemini"),
        ("services.facebook", "facebook"),
        ("services.news_scraper", "news_scraper"),
    ]
    for svc_name, attr in _v9_singletons:
        try:
            svc_module = __import__(svc_name, fromlist=[""])
            obj = getattr(svc_module, attr, None)
            if obj and hasattr(obj, "close"):
                obj.close()
        except Exception:
            pass

    logger.info("Graceful shutdown complete.")
