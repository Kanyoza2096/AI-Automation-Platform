"""
Worker entry point for non-API Render services.
Includes a minimal HTTP health server so Render detects the port.

v14.6 — SERVICE_MODE-aware worker with Director HTTP routes:
  - "workers"    → BrandScheduler only
  - "monitor"    → Monitor + Backup + Trends
  - "github"     → GitHub Connector only
  - "director"   → Director only (WITH HTTP routes on port)
  - "meta"       → MetaEngine only
  - "condition"  → ConditionEngine only
  - "core"       → Director + MetaEngine + ConditionEngine (WITH HTTP routes)
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
    """Start minimal HTTP server for Render port detection."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"Health server listening on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server failed: {e}")


def start_director_http_server(container):
    """Start FastAPI server with Director routes for director mode."""
    import uvicorn
    from fastapi import FastAPI
    from services.director import create_app as create_director_app, director as dir_instance

    if not dir_instance:
        logger.error("Director instance not available — cannot start HTTP server")
        return

    director_app = create_director_app(dir_instance)

    app = FastAPI(title="Kanyoza Director Service", version=dir_instance.VERSION)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "mode": SERVICE_MODE, "service": "director"}

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "director", "version": dir_instance.VERSION}

    # Mount Director app at root so ALL routes work: /status, /tasks, /decisions, etc.
    app.mount("/", director_app)

    logger.info(f"Director HTTP server starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def start_core_http_server(container):
    """Start FastAPI server with Director + MetaEngine routes for core mode."""
    import uvicorn
    from fastapi import FastAPI
    from services.director import create_app as create_director_app, director as dir_instance
    from services.meta_engine import meta_engine

    app = FastAPI(title="Kanyoza Core Service", version="6.0.1")

    @app.get("/health")
    async def health():
        return {"status": "healthy", "mode": SERVICE_MODE, "service": "core"}

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "core"}

    # Mount Director at /director
    if dir_instance:
        director_app = create_director_app(dir_instance)
        app.mount("/director", director_app)
        logger.info("Director routes mounted at /director")

    # MetaEngine routes
    @app.get("/meta/status")
    async def meta_status():
        return meta_engine.get_status()

    @app.get("/meta/dependency-graph")
    async def meta_graph():
        return meta_engine.get_dependency_graph()

    @app.get("/meta/root-cause")
    async def meta_root_cause():
        return {"root_cause": meta_engine.get_root_cause()}

    @app.get("/meta/predictions")
    async def meta_predictions():
        return {"predictions": meta_engine.get_cascading_predictions()}

    @app.get("/meta/impact")
    async def meta_impact():
        return meta_engine.get_impact_assessment()

    @app.get("/meta/recovery-plans")
    async def meta_recovery():
        return {"recovery_plans": meta_engine.get_recovery_plans()}

    @app.get("/meta/incidents")
    async def meta_incidents():
        return {"incidents": meta_engine.get_incidents()}

    logger.info(f"Core HTTP server starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def run_worker():
    """Run services based on SERVICE_MODE."""
    from dependency_registry import build_container
    from services.supabase import get_service_client_from_settings, create_fresh_service_client

    container = build_container()
    supabase_client = get_service_client_from_settings()

    # ── Phase 0: Infrastructure (always needed) ───────────────────
    from lifecycle import _boot_event_bus
    _boot_event_bus(container)

    from lifecycle import _boot_supabase
    _boot_supabase(container)

    from lifecycle import _boot_redis
    _boot_redis(container)

    # Initialize JobStatsService (fixes warning on all instances)
    try:
        from services.job_stats_service import init_job_stats_service
        init_job_stats_service(
            supabase_client=create_fresh_service_client,
            event_bus=None,
        )
        logger.info("JobStatsService initialized")
    except Exception as exc:
        logger.debug("JobStatsService init skipped: %s", exc)

    # ── Conditional: AIOrchestrator + ContentEngine ────────────────
    if SERVICE_MODE in ("core", "director", "meta", "condition"):
        from lifecycle import _boot_ai_orchestrator
        _boot_ai_orchestrator(container)

        from lifecycle import _boot_content_engine
        _boot_content_engine(container)

    # ── Individual services ───────────────────────────────────────
    if SERVICE_MODE == "director":
        from lifecycle import _boot_director
        _boot_director(container)
        logger.info("Worker: Director booted — starting HTTP server")
        start_director_http_server(container)

    elif SERVICE_MODE == "meta":
        from lifecycle import _boot_meta_engine
        _boot_meta_engine(container)
        logger.info("Worker: MetaEngine only started")
        while True:
            time.sleep(60)

    elif SERVICE_MODE == "condition":
        from lifecycle import _boot_condition_engine
        _boot_condition_engine(container)
        logger.info("Worker: ConditionEngine only started")
        while True:
            time.sleep(60)

    elif SERVICE_MODE == "core":
        from lifecycle import _boot_meta_engine
        _boot_meta_engine(container)

        from lifecycle import _boot_director
        _boot_director(container)

        from lifecycle import _boot_condition_engine
        _boot_condition_engine(container)
        logger.info("Worker: Core services booted — starting HTTP server")
        start_core_http_server(container)

    elif SERVICE_MODE == "workers":
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = container.resolve(BrandSchedulerManager)
        manager.set_container(container)
        manager.start()
        logger.info("Worker: BrandSchedulerManager started")
        while True:
            time.sleep(60)

    elif SERVICE_MODE == "monitor":
        from plugins.monitor.plugin import init_monitor_plugin
        init_monitor_plugin(supabase_client=supabase_client)

        from plugins.backup.plugin import init_backup_plugin
        init_backup_plugin(supabase_client=supabase_client)

        from plugins.trend_intelligence.plugin import init_trend_plugin
        init_trend_plugin(supabase_client=supabase_client)
        logger.info("Worker: Monitor plugins started")
        while True:
            time.sleep(60)

    elif SERVICE_MODE == "github":
        from plugins.github_connector.plugin import init_github_connector_plugin
        init_github_connector_plugin(supabase_client=supabase_client)
        logger.info("Worker: GitHub Connector started")
        while True:
            time.sleep(60)

    else:
        logger.error(f"Unknown SERVICE_MODE: {SERVICE_MODE}")
        raise SystemExit(1)


def main():
    logger.info(f"Worker starting in mode: {SERVICE_MODE}")

    # For director and core modes, FastAPI serves HTTP (no need for separate health server)
    if SERVICE_MODE in ("director", "core"):
        run_worker()
    else:
        # Minimal health server for other modes
        health_thread = threading.Thread(
            target=start_health_server,
            daemon=True,
            name="health-server",
        )
        health_thread.start()

        # Give health server a moment to bind
        time.sleep(1)

        run_worker()


if __name__ == "__main__":
    main()
