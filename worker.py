"""
Worker entry point for distributed free-tier deployment.
Runs background services based on SERVICE_MODE.
"""

import logging
import time
import os

logger = logging.getLogger("kanyoza.worker")

SERVICE_MODE = os.getenv("SERVICE_MODE", "full")


def run_scheduler_mode():
    """Service 2: BrandScheduler only."""
    from dependency_registry import build_container
    from config.settings import settings
    
    container = build_container()
    
    from scheduler.brand_scheduler import BrandSchedulerManager
    manager = container.resolve(BrandSchedulerManager)
    manager.set_container(container)
    manager.start()
    
    logger.info("Worker (scheduler mode): started")
    
    # Keep alive
    while True:
        time.sleep(60)


def run_monitor_mode():
    """Service 3: Monitor + Backup + Trends."""
    from services.supabase import get_service_client_from_settings
    
    client = get_service_client_from_settings()
    
    from plugins.monitor.plugin import init_monitor_plugin
    init_monitor_plugin(supabase_client=client)
    logger.info("Worker (monitor mode): MonitorPlugin started")
    
    from plugins.backup.plugin import init_backup_plugin
    init_backup_plugin(supabase_client=client)
    logger.info("Worker (monitor mode): BackupPlugin started")
    
    from plugins.trend_intelligence.plugin import init_trend_plugin
    init_trend_plugin(supabase_client=client)
    logger.info("Worker (monitor mode): TrendIntelligence started")
    
    # Keep alive
    while True:
        time.sleep(60)


def run_github_mode():
    """Service 4: GitHub Connector only."""
    from services.supabase import get_service_client_from_settings
    
    client = get_service_client_from_settings()
    
    from plugins.github_connector.plugin import init_github_connector_plugin
    init_github_connector_plugin(supabase_client=client)
    logger.info("Worker (github mode): GitHubConnector started")
    
    # Keep alive
    while True:
        time.sleep(60)


def main():
    logger.info(f"Worker starting in mode: {SERVICE_MODE}")
    
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
