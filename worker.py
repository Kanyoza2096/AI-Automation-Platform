"""
Worker entry point for non-API Render services.
Includes a minimal HTTP health server so Render detects the port.
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
            self.wfile.write(b'{"status":"healthy","mode":"' + SERVICE_MODE.encode() + b'"}')
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logs


def start_health_server():
    """Start a minimal HTTP server for Render port detection."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"Health server listening on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server failed: {e}")


def run_scheduler_mode():
    """BrandScheduler only."""
    from dependency_registry import build_container
    container = build_container()
    
    from scheduler.brand_scheduler import BrandSchedulerManager
    manager = container.resolve(BrandSchedulerManager)
    manager.set_container(container)
    manager.start()
    logger.info("Worker (scheduler mode): started")
    
    while True:
        time.sleep(60)


def run_monitor_mode():
    """Monitor + Backup + Trends."""
    from services.supabase import get_service_client_from_settings
    client = get_service_client_from_settings()
    
    from plugins.monitor.plugin import init_monitor_plugin
    init_monitor_plugin(supabase_client=client)
    
    from plugins.backup.plugin import init_backup_plugin
    init_backup_plugin(supabase_client=client)
    
    from plugins.trend_intelligence.plugin import init_trend_plugin
    init_trend_plugin(supabase_client=client)
    
    logger.info("Worker (monitor mode): all started")
    
    while True:
        time.sleep(60)


def run_github_mode():
    """GitHub Connector only."""
    from services.supabase import get_service_client_from_settings
    client = get_service_client_from_settings()
    
    from plugins.github_connector.plugin import init_github_connector_plugin
    init_github_connector_plugin(supabase_client=client)
    logger.info("Worker (github mode): started")
    
    while True:
        time.sleep(60)


def main():
    logger.info(f"Worker starting in mode: {SERVICE_MODE}")
    
    # Start health server in background thread
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
        name="health-server",
    )
    health_thread.start()
    
    # Start the actual worker
    if SERVICE_MODE == "workers":
        run_scheduler_mode()
    elif SERVICE_MODE == "monitor":
        run_monitor_mode()
    elif SERVICE_MODE == "github":
        run_github_mode()
    else:
        logger.error(f"Unknown SERVICE_MODE: {SERVICE_MODE}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
