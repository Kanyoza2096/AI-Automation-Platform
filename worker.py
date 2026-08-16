"""
Worker entry point for non-API Render services.
Includes a minimal HTTP health server so Render detects the port.

v14.5 — SERVICE_MODE-aware worker:
  - "workers"    → BrandScheduler only
  - "monitor"    → Monitor + Backup + Trends
  - "github"     → GitHub Connector only
  - "director"   → Director only
  - "meta"       → MetaEngine only
  - "condition"  → ConditionEngine only
  - "core"       → Director + MetaEngine + ConditionEngine
"""

import logging
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger("kanyoza.worker")

SERVICE_MODE = os.getenv("SERVICE_MODE", "full")
PORT = int(os.getenv("PORT", "10000"))


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for Render port detection."""

    def do_GET(self):
        if self.path in ("/health", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"status":"healthy","mode":"'
                + SERVICE_MODE.encode()
                + b'"}'
            )
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"Health server listening on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server failed: {e}")


def run_worker():
    """Run services based on SERVICE_MODE."""
    from dependency_registry import build_container
    from services.supabase import get_service_client_from_settings

    container = build_container()
    supabase_client = get_service_client_from_settings()

    # ── Phase 0: Infrastructure (always needed) ───────────────────
    from lifecycle import _boot_event_bus
    _boot_event_bus(container)

    from lifecycle import _boot_supabase
    _boot_supabase(container)

    from lifecycle import _boot_redis
    _boot_redis(container)

    # ── Conditional: AIOrchestrator + ContentEngine (needed by engines) ──
    if SERVICE_MODE in ("core", "director", "meta", "condition"):
        from lifecycle import _boot_ai_orchestrator
        _boot_ai_orchestrator(container)

        from lifecycle import _boot_content_engine
        _boot_content_engine(container)

    # ── Individual services ───────────────────────────────────────
    if SERVICE_MODE == "director":
        from lifecycle import _boot_director
        _boot_director(container)
        logger.info("Worker: Director only started")

    elif SERVICE_MODE == "meta":
        from lifecycle import _boot_meta_engine
        _boot_meta_engine(container)
        logger.info("Worker: MetaEngine only started")

    elif SERVICE_MODE == "condition":
        from lifecycle import _boot_condition_engine
        _boot_condition_engine(container)
        logger.info("Worker: ConditionEngine only started")

    elif SERVICE_MODE == "core":
        from lifecycle import _boot_meta_engine
        _boot_meta_engine(container)

        from lifecycle import _boot_director
        _boot_director(container)

        from lifecycle import _boot_condition_engine
        _boot_condition_engine(container)
        logger.info("Worker: Director + MetaEngine + ConditionEngine started")

    elif SERVICE_MODE == "workers":
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = container.resolve(BrandSchedulerManager)
        manager.set_container(container)
        manager.start()
        logger.info("Worker: BrandSchedulerManager started")

    elif SERVICE_MODE == "monitor":
        from plugins.monitor.plugin import init_monitor_plugin
        init_monitor_plugin(supabase_client=supabase_client)

        from plugins.backup.plugin import init_backup_plugin
        init_backup_plugin(supabase_client=supabase_client)

        from plugins.trend_intelligence.plugin import init_trend_plugin
        init_trend_plugin(supabase_client=supabase_client)
        logger.info("Worker: Monitor plugins started")

    elif SERVICE_MODE == "github":
        from plugins.github_connector.plugin import init_github_connector_plugin
        init_github_connector_plugin(supabase_client=supabase_client)
        logger.info("Worker: GitHub Connector started")

    else:
        logger.error(f"Unknown SERVICE_MODE: {SERVICE_MODE}")
        raise SystemExit(1)

    # Keep alive
    while True:
        time.sleep(60)


def main():
    logger.info(f"Worker starting in mode: {SERVICE_MODE}")

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
        name="health-server",
    )
    health_thread.start()

    run_worker()


if __name__ == "__main__":
    main()
