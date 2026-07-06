"""
Kanyoza Systems AI Platform v10 — Bootstrap.

Single entry-point for application initialisation.
Coordinates the startup sequence:
  1. Logging
  2. Configuration validation
  3. Dependency injection
  4. Flask app creation
  5. Lifecycle registration

Only main.py should call bootstrap().
"""

from __future__ import annotations

import logging

# Step 1 — Logging must be configured before any other import that logs
from config.logging_config import setup_logging
from config.settings import settings

setup_logging(settings.log_level)

logger = logging.getLogger("kanyoza.bootstrap")


def bootstrap():
    """
    Initialise the platform and return a ready-to-serve Flask app.

    Raises SystemExit on missing required configuration.
    """
    logger.info("=" * 60)
    logger.info("Kanyoza Systems AI Platform v10  —  starting up")
    logger.info("=" * 60)
    logger.info("Environment : %s", settings.environment)
    logger.info("Log level   : %s", settings.log_level)

    # Step 2 — Validate required secrets before anything else
    logger.info("Validating configuration...")
    settings.validate_required()
    logger.info(
        "Config OK  (gemini=%s  facebook=%s  supabase=%s)",
        bool(settings.gemini_key),
        bool(settings.page_access_token),
        settings.supabase_enabled,
    )

    # Step 3 — Build dependency injection container
    logger.info("Building service container...")
    from dependency_registry import build_container
    container = build_container()

    # Step 4 — Create Flask application
    logger.info("Creating Flask application...")
    from application_factory import create_app
    app = create_app(settings=settings, container=container)

    # Step 5 — Register lifecycle hooks (scheduler start/stop, etc.)
    logger.info("Registering lifecycle hooks...")
    from lifecycle import register_lifecycle
    register_lifecycle(app, container)

    logger.info("Bootstrap complete — application ready.")
    return app
