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

# Track whether shutdown has already been triggered (prevent double-shutdown)
_shutdown_requested = False


def register_lifecycle(app: Flask, container: ServiceContainer) -> None:
    """
    Register all application lifecycle callbacks.

    Called once during bootstrap().
    """
    _start_schedulers(container)
    _register_signal_handlers(container)

    @app.teardown_appcontext
    def _on_teardown(exc):
        """Called by Flask after each request context tears down."""
        # Per-request cleanup goes here (connection return to pool, etc.)
        pass

    logger.info("Lifecycle hooks registered.")


# ---------------------------------------------------------------------------
# Scheduler startup
# ---------------------------------------------------------------------------


def _start_schedulers(container: ServiceContainer) -> None:
    """
    Start the v9 post scheduler (compatibility) and any v10 schedulers.
    Errors are logged as warnings — a scheduler failure must not crash startup.
    """
    _start_v9_post_scheduler(container)
    _start_v9_guardian_scheduler(container)


def _start_v9_post_scheduler(container: ServiceContainer) -> None:
    """Start the legacy v9 post scheduler while the v10 scheduler is built."""
    try:
        from scheduler.post_scheduler import scheduler as post_scheduler

        # Wire Supabase if available
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
    """Start the legacy v9 Code Guardian scheduler when configured."""
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
    """Register SIGTERM / SIGINT handlers for graceful shutdown."""

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

    # v9 schedulers
    try:
        from scheduler.post_scheduler import scheduler as post_scheduler
        post_scheduler.stop(timeout=30)
        logger.info("Post scheduler stopped.")
    except Exception as exc:
        logger.warning("Error stopping post scheduler: %s", exc)

    # v9 services — each module exposes a named singleton with .close()
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
