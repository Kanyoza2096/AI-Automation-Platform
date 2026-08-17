"""
Worker entry point for non-API Render services.
Includes a minimal HTTP health server so Render detects the port.

v14.7 — Full Autonomous System:
  - SharedEventBus: Cross-instance communication
  - Autonomous Content Engine: Self-driving content generation
  - All instances connected, posting never stops

SERVICE_MODE values:
  - "workers"    → BrandScheduler + Autonomous Content Engine
  - "monitor"    → Monitor + Backup + Trends
  - "github"     → GitHub Connector only
  - "director"   → Director only (WITH HTTP routes)
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
    from fastapi.responses import JSONResponse
    from services.director import create_app as create_director_app, director as dir_instance

    if not dir_instance:
        logger.error("Director instance not available — cannot start HTTP server")
        return

    director_app = create_director_app(dir_instance)

    app = FastAPI(title="Kanyoza Director Service", version=dir_instance.VERSION)

    @app.get("/health")
    async def health():
        return JSONResponse(content={"status": "healthy", "mode": SERVICE_MODE, "service": "director"}, status_code=200)

    @app.get("/")
    async def root():
        return JSONResponse(content={"status": "ok", "service": "director", "version": dir_instance.VERSION}, status_code=200)

    app.mount("/", director_app)

    logger.info(f"Director HTTP server starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def start_core_http_server(container):
    """Start FastAPI server with Director + MetaEngine routes for core mode."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from services.director import create_app as create_director_app, director as dir_instance
    from services.meta_engine import meta_engine

    app = FastAPI(title="Kanyoza Core Service", version="6.0.1")

    @app.get("/health")
    async def health():
        return JSONResponse(content={"status": "healthy", "mode": SERVICE_MODE, "service": "core"}, status_code=200)

    @app.get("/")
    async def root():
        return JSONResponse(content={"status": "ok", "service": "core"}, status_code=200)

    if dir_instance:
        director_app = create_director_app(dir_instance)
        app.mount("/director", director_app)

    @app.get("/meta/status")
    async def meta_status():
        return JSONResponse(content=meta_engine.get_status(), status_code=200)

    logger.info(f"Core HTTP server starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


# ═══════════════════════════════════════════════════════════════════════════
# v14.7 — SHARED EVENT BUS HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

def register_shared_event_handlers(shared_bus, container=None):
    """Register event handlers based on SERVICE_MODE."""
    
    if SERVICE_MODE == "director":
        _register_director_handlers(shared_bus, container)
    elif SERVICE_MODE == "workers":
        _register_workers_handlers(shared_bus, container)
    elif SERVICE_MODE == "meta":
        _register_meta_handlers(shared_bus, container)
    elif SERVICE_MODE == "condition":
        _register_condition_handlers(shared_bus, container)
    elif SERVICE_MODE == "monitor":
        _register_monitor_handlers(shared_bus, container)
    elif SERVICE_MODE == "github":
        _register_github_handlers(shared_bus, container)
    elif SERVICE_MODE == "core":
        _register_director_handlers(shared_bus, container)
        _register_meta_handlers(shared_bus, container)
        _register_condition_handlers(shared_bus, container)


def _register_director_handlers(shared_bus, container):
    """Director listens for alerts and results."""
    from services.director import director
    
    def handle_engine_alert(payload):
        engine_name = payload.get("engine", "unknown")
        status = payload.get("status", "unknown")
        message = payload.get("message", "")
        logger.info(f"👔 Director received alert: {engine_name} is {status} — {message}")
        
        if director:
            director.enqueue_event("engine_alert", {
                "engine": engine_name,
                "status": status,
                "message": message,
            })
    
    def handle_post_published(payload):
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")
        logger.info(f"👔 Director: Post published — {topic} for {brand}")
        if director:
            director.enqueue_event("post_published", payload)
    
    def handle_content_failed(payload):
        error = payload.get("error", "Unknown error")
        brand = payload.get("brand", "")
        logger.warning(f"👔 Director: Content failed for {brand} — {error}")
        if director:
            director.enqueue_event("content_failed", payload)
    
    def handle_engagement_update(payload):
        brand = payload.get("brand", "")
        engagement = payload.get("engagement", 0)
        logger.info(f"👔 Director: Engagement update — {brand}: {engagement}")
        if director:
            director.enqueue_event("engagement_update", payload)
    
    def handle_trend_analysis(payload):
        topic = payload.get("topic", "")
        logger.info(f"👔 Director: Trend analysis requested — {topic}")
        if director:
            director.enqueue_event("trend_analysis", payload)
    
    def handle_strategy_recommendation(payload):
        action = payload.get("action", "")
        reason = payload.get("reason", "")
        logger.info(f"👔 Director: Strategy recommendation — {action}: {reason}")
        if director:
            director.enqueue_event("strategy_update", payload)
    
    shared_bus.subscribe("engine_alert", handle_engine_alert)
    shared_bus.subscribe("post_published", handle_post_published)
    shared_bus.subscribe("content_failed", handle_content_failed)
    shared_bus.subscribe("engagement_update", handle_engagement_update)
    shared_bus.subscribe("trend_analysis_request", handle_trend_analysis)
    shared_bus.subscribe("strategy_recommendation", handle_strategy_recommendation)
    logger.info("SharedEventBus: Director handlers registered")


def _register_workers_handlers(shared_bus, container):
    """Workers listen for publish commands and handle them."""
    
    def handle_publish_request(payload):
        brand_id = payload.get("brand_id", "")
        topic = payload.get("topic", "")
        brand_name = payload.get("brand_name", "")
        
        logger.info(f"📤 Workers: Publish requested — '{topic}' for {brand_name}")
        
        try:
            from scheduler.brand_scheduler import BrandSchedulerManager
            manager = container.resolve(BrandSchedulerManager) if container else None
            
            if manager and brand_id:
                result = manager.trigger_for_brand(brand_id)
                logger.info(f"📤 Workers: Publish result — {result}")
                
                if result.get("ok"):
                    shared_bus.publish("post_published", {
                        "brand": brand_name,
                        "brand_id": brand_id,
                        "topic": result.get("topic", topic),
                        "platforms": result.get("platforms", []),
                    })
                else:
                    shared_bus.publish("content_failed", {
                        "brand": brand_name,
                        "error": result.get("error", "Publish failed"),
                    })
            else:
                # If no manager, just log — the Autonomous Content Engine will retry
                logger.warning(f"📤 Workers: No BrandSchedulerManager available for {brand_id}")
        except Exception as exc:
            logger.error(f"📤 Workers: Publish handling failed — {exc}")
            shared_bus.publish("content_failed", {
                "brand": brand_name,
                "error": str(exc),
            })
    
    def handle_publish_command(payload):
        """Direct publish command from Autonomous Content Engine."""
        handle_publish_request(payload)
    
    shared_bus.subscribe("publish_request", handle_publish_request)
    shared_bus.subscribe("publish_command", handle_publish_command)
    logger.info("SharedEventBus: Workers handlers registered")


def _register_meta_handlers(shared_bus, container):
    """MetaEngine listens for status updates."""
    from services.meta_engine import meta_engine
    
    def handle_engine_status_update(payload):
        engine_name = payload.get("engine", "unknown")
        status = payload.get("status", "unknown")
        logger.info(f"🔍 MetaEngine: Status update — {engine_name} is {status}")
        
        if meta_engine:
            try:
                meta_engine.record_metric(engine_name, "status", 1.0 if status == "healthy" else 0.0)
            except Exception:
                pass
    
    def handle_service_heartbeat(payload):
        service = payload.get("service", "unknown")
        logger.debug(f"🔍 MetaEngine: Heartbeat from {service}")
        if meta_engine:
            try:
                meta_engine.record_success(service)
            except Exception:
                pass
    
    def handle_incident_report(payload):
        incident = payload.get("incident", "")
        logger.warning(f"🔍 MetaEngine: Incident reported — {incident}")
        shared_bus.publish("engine_alert", {
            "engine": payload.get("engine", "unknown"),
            "status": "degraded",
            "message": incident,
        })
    
    shared_bus.subscribe("engine_status_update", handle_engine_status_update)
    shared_bus.subscribe("service_heartbeat", handle_service_heartbeat)
    shared_bus.subscribe("incident_report", handle_incident_report)
    logger.info("SharedEventBus: MetaEngine handlers registered")


def _register_condition_handlers(shared_bus, container):
    """ConditionEngine listens for posts and engagement."""
    
    def handle_post_published(payload):
        topic = payload.get("topic", "")
        brand = payload.get("brand", "")
        logger.info(f"🧠 ConditionEngine: Analyzing post — {topic} for {brand}")
        shared_bus.publish("strategy_recommendation", {
            "brand": brand,
            "action": "monitor",
            "reason": f"Post published: {topic}",
        })
    
    def handle_engagement(payload):
        brand = payload.get("brand", "")
        engagement = payload.get("engagement", 0)
        logger.info(f"🧠 ConditionEngine: Engagement data — {brand}: {engagement}")
        
        if engagement > 100:
            shared_bus.publish("strategy_recommendation", {
                "brand": brand,
                "action": "increase_similar_content",
                "reason": f"High engagement ({engagement})",
            })
        elif engagement < 5:
            shared_bus.publish("strategy_recommendation", {
                "brand": brand,
                "action": "change_content_strategy",
                "reason": f"Low engagement ({engagement})",
            })
    
    shared_bus.subscribe("post_published", handle_post_published)
    shared_bus.subscribe("engagement_data", handle_engagement)
    logger.info("SharedEventBus: ConditionEngine handlers registered")


def _register_monitor_handlers(shared_bus, container):
    """Monitor plugins listen for check requests."""
    
    def handle_check_request(payload):
        service = payload.get("service", "")
        logger.info(f"📡 Monitor: Check requested for {service}")
        shared_bus.publish("service_heartbeat", {"service": service, "status": "healthy"})
    
    def handle_trend_detected(payload):
        topic = payload.get("topic", "")
        logger.info(f"📡 Monitor: Trend detected — {topic}")
        # Forward to Director
        shared_bus.publish("trend_analysis_request", {
            "topic": topic,
            "source": payload.get("source", "trend_intelligence"),
        })
    
    shared_bus.subscribe("check_request", handle_check_request)
    shared_bus.subscribe("trend_detected", handle_trend_detected)
    logger.info("SharedEventBus: Monitor handlers registered")


def _register_github_handlers(shared_bus, container):
    """GitHub Connector listens for code tasks."""
    
    def handle_code_fix_request(payload):
        repo = payload.get("repo", "")
        issue = payload.get("issue", "")
        logger.info(f"🔧 GitHub: Fix requested for {repo} — {issue}")
    
    shared_bus.subscribe("code_fix_request", handle_code_fix_request)
    logger.info("SharedEventBus: GitHub handlers registered")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN WORKER
# ═══════════════════════════════════════════════════════════════════════════

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

    # Initialize JobStatsService
    try:
        from services.job_stats_service import init_job_stats_service
        init_job_stats_service(
            supabase_client=create_fresh_service_client,
            event_bus=None,
        )
        logger.info("JobStatsService initialized")
    except Exception as exc:
        logger.debug("JobStatsService init skipped: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # v14.7 — Initialize SharedEventBus
    # ═══════════════════════════════════════════════════════════════
    from core.shared_event_bus import init_shared_event_bus
    
    shared_bus = init_shared_event_bus(
        supabase_client=get_service_client_from_settings(),
        source_service=SERVICE_MODE,
    )
    
    # Register event handlers for this instance's role
    register_shared_event_handlers(shared_bus, container)
    
    # Publish startup heartbeat
    shared_bus.publish("service_heartbeat", {
        "service": SERVICE_MODE,
        "status": "started",
    })
    logger.info(f"SharedEventBus: {SERVICE_MODE} connected to autonomous network")

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
        logger.info("Worker: MetaEngine started — monitoring and communicating")
        _keep_alive_with_heartbeat(shared_bus, "meta")

    elif SERVICE_MODE == "condition":
        from lifecycle import _boot_condition_engine
        _boot_condition_engine(container)
        logger.info("Worker: ConditionEngine started")
        _keep_alive_with_heartbeat(shared_bus, "condition")

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

        # ═══════════════════════════════════════════════════════════
        # v14.7 — Start Autonomous Content Engine
        # ═══════════════════════════════════════════════════════════
        from core.autonomous_content import init_autonomous_content
        
        content_engine = init_autonomous_content(
            supabase_client=get_service_client_from_settings(),
            shared_bus=shared_bus,
            container=container,
        )
        logger.info("Worker: Autonomous Content Engine started — will never stop posting")
        
        _keep_alive_with_heartbeat(shared_bus, "workers")

    elif SERVICE_MODE == "monitor":
        from plugins.monitor.plugin import init_monitor_plugin
        init_monitor_plugin(supabase_client=supabase_client)

        from plugins.backup.plugin import init_backup_plugin
        init_backup_plugin(supabase_client=supabase_client)

        from plugins.trend_intelligence.plugin import init_trend_plugin
        init_trend_plugin(supabase_client=supabase_client)
        logger.info("Worker: Monitor plugins started")
        _keep_alive_with_heartbeat(shared_bus, "monitor")

    elif SERVICE_MODE == "github":
        from plugins.github_connector.plugin import init_github_connector_plugin
        init_github_connector_plugin(supabase_client=supabase_client)
        logger.info("Worker: GitHub Connector started")
        _keep_alive_with_heartbeat(shared_bus, "github")

    else:
        logger.error(f"Unknown SERVICE_MODE: {SERVICE_MODE}")
        raise SystemExit(1)


def _keep_alive_with_heartbeat(shared_bus, service_name):
    """Keep the worker alive and send heartbeats."""
    while True:
        time.sleep(60)
        try:
            shared_bus.publish("service_heartbeat", {
                "service": service_name,
                "status": "healthy",
                "timestamp": time.time(),
            })
        except Exception:
            pass


def main():
    logger.info(f"Worker starting in mode: {SERVICE_MODE}")

    if SERVICE_MODE in ("director", "core"):
        run_worker()
    else:
        health_thread = threading.Thread(
            target=start_health_server,
            daemon=True,
            name="health-server",
        )
        health_thread.start()
        time.sleep(1)
        run_worker()


if __name__ == "__main__":
    main()
