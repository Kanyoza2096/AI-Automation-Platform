"""
Kanyoza Systems AI Platform v14.7 — FastAPI Entry Point.

Orchestrates application creation, middleware, route mounting, and the
phased boot sequence via the lifecycle module.

v14.7 EVENT API INTEGRATION:
- /api/v1/events/* routes mounted for event observability
- Event API initialized after EventBus boot
- SSE stream for real-time event monitoring

v14.6 DIRECTOR PROXY:
- /director/* routes proxy to the Director instance.
- Dashboard can call Director endpoints from the API instance.

v14.5 SERVICE_MODE FIX:
- Removed duplicate service initialization.
- _build_boot_sequence handles ALL services.

v14.0 FIXES (retained):
- _safe_publish() logs EventBus failures.
- _stop_director() logs shutdown failures.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from service_container import ServiceContainer
from dependency_registry import build_container
from lifecycle import (
    boot_infrastructure,
    graceful_shutdown,
    health_registry,
    shutdown_registry,
    HealthStatus,
)
from core.socket_server import create_socket_app


logger = logging.getLogger("kanyoza.main")


# ── Suppress known library noise ────────────────────────────────────────────

logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")


# ── Director service URL ─────────────────────────────────────────────────────

DIRECTOR_SERVICE_URL = os.getenv(
    "DIRECTOR_SERVICE_URL",
    "https://omnexa-director.onrender.com",
).strip().rstrip("/")


def _get_service_mode() -> str:
    """Get SERVICE_MODE from environment or settings."""
    mode = os.getenv("SERVICE_MODE", "").strip().lower()

    if not mode:
        mode = getattr(settings, "service_mode", "full")

    return mode


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP METADATA
# ═══════════════════════════════════════════════════════════════════════════

_startup_started_at: Optional[datetime] = None
_startup_completed_at: Optional[datetime] = None
_startup_duration_seconds: float = 0.0
_boot_warnings: list[dict[str, Any]] = []


def _record_boot_warning(service: str, exc: Exception) -> None:
    """Record a startup warning for health reporting."""
    warning = {
        "service": service,
        "error": str(exc)[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _boot_warnings.append(warning)

    logger.warning(
        "Boot warning [%s]: %s",
        service,
        exc,
    )


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════


class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Tracks daily API call counts in Redis."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            try:
                container = getattr(
                    request.app.state,
                    "container",
                    None,
                )

                if container:
                    from core.redis_client import UpstashRedisClient

                    redis = container.try_resolve(
                        UpstashRedisClient
                    )

                    if redis and redis.is_available:
                        today = datetime.now(
                            timezone.utc
                        ).strftime("%Y-%m-%d")

                        key = f"metrics:api_calls:{today}"

                        count = redis.incr(key)

                        if count == 1:
                            redis.expire(
                                key,
                                86400 * 2,
                            )

            except Exception:
                # Metrics must never break an API request.
                pass

        return await call_next(request)


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Returns 503 until boot has completed."""

    async def dispatch(self, request: Request, call_next):
        if not getattr(
            request.app.state,
            "boot_completed",
            False,
        ):
            if request.url.path in (
                "/health",
                "/health/live",
                "/health/ready",
            ):
                return await call_next(request)

            return Response(
                content=json.dumps(
                    {
                        "status": "starting",
                        "message": (
                            "Application is still initializing. "
                            "Please retry."
                        ),
                    }
                ),
                status_code=503,
                media_type="application/json",
                headers={
                    "Retry-After": "5",
                },
            )

        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# DIRECTOR PROXY
# ═══════════════════════════════════════════════════════════════════════════


def _mount_director_proxy(app: FastAPI) -> None:
    """
    Mount /director/* as a reverse proxy to the Director service.
    """
    # ... (existing implementation unchanged) ...


# ═══════════════════════════════════════════════════════════════════════════
# EVENT API MOUNTING (NEW v14.7)
# ═══════════════════════════════════════════════════════════════════════════

def _mount_event_api(app: FastAPI) -> None:
    """Mount Event API routes for real-time event observability."""
    try:
        from routes.event_api import (
            register_event_api_routes,
        )

        register_event_api_routes(app)

        logger.info(
            "Event API routes mounted "
            "(/api/v1/events/*)."
        )

    except ImportError:
        logger.debug(
            "Event API routes not available — skipping."
        )

    except Exception as exc:
        logger.warning(
            "Event API mount failed: %s",
            str(exc)[:300],
        )


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_started_at
    global _startup_completed_at
    global _startup_duration_seconds

    _startup_started_at = datetime.now(timezone.utc)

    startup_start = time.monotonic()

    service_mode = _get_service_mode()

    # Reset per-process startup state.
    _boot_warnings.clear()

    app.state.boot_completed = False
    app.state.boot_warnings = []

    logger.info("=" * 60)
    logger.info(
        "Kanyoza AI Platform v14.7 — Starting up..."
    )
    logger.info(
        "  Environment : %s",
        settings.environment,
    )
    logger.info(
        "  Service Mode: %s",
        service_mode,
    )
    logger.info(
        "  Log level   : %s",
        settings.log_level,
    )
    logger.info(
        "  Python      : %s",
        _get_python_version(),
    )
    logger.info("=" * 60)

    settings.validate_required()

    logger.info(
        "Building service container..."
    )

    container: ServiceContainer = build_container()

    app.state.container = container

    redis_client = _resolve_redis(container)

    # All service initialization belongs to lifecycle.py.
    try:
        boot_infrastructure(
            app,
            container,
        )

        logger.info(
            "Phased boot sequence complete."
        )

    except SystemExit:
        raise

    except Exception as exc:
        _record_boot_warning(
            "boot_sequence",
            exc,
        )

    # ═══════════════════════════════════════════════════════════════
    # INITIALIZE EVENT API AFTER BOOT (NEW v14.7)
    # ═══════════════════════════════════════════════════════════════
    if service_mode in ("full", "api", ""):
        try:
            from routes.event_api import (
                init_event_api,
            )

            init_event_api()

            logger.info(
                "Event API initialized — "
                "capturing live events."
            )

        except ImportError:
            logger.debug(
                "Event API not available — "
                "event capture skipped."
            )

        except Exception as exc:
            logger.warning(
                "Event API initialization failed: %s",
                str(exc)[:200],
            )

    # API-local stores.
    if service_mode in (
        "full",
        "api",
        "",
    ):
        _init_api_stores(
            app,
            redis_client,
        )

    cleanup_task: Optional[
        asyncio.Task
    ] = None

    if service_mode in (
        "full",
        "api",
        "",
    ):
        cleanup_task = asyncio.create_task(
            _draft_cleanup_loop(app),
            name="draft-cleanup-loop",
        )

        shutdown_registry.register(
            "DraftCleanup",
            lambda: (
                cleanup_task.cancel()
                if cleanup_task
                and not cleanup_task.done()
                else None
            ),
            timeout_seconds=5.0,
        )

    app.state.boot_completed = True

    app.state.boot_warnings = list(
        _boot_warnings
    )

    _startup_completed_at = datetime.now(
        timezone.utc
    )

    _startup_duration_seconds = (
        time.monotonic() - startup_start
    )

    if _boot_warnings:
        logger.warning(
            "Boot completed with %d warning(s) in %.2fs:",
            len(_boot_warnings),
            _startup_duration_seconds,
        )

        for warning in _boot_warnings:
            logger.warning(
                "  ⚠ %s: %s",
                warning["service"],
                warning["error"],
            )

    else:
        logger.info(
            "Boot complete — all configured services "
            "initialized (%.2fs).",
            _startup_duration_seconds,
        )

    logger.info(
        "Application ready to accept requests."
    )

    logger.info("=" * 60)

    try:
        yield

    finally:
        logger.info("=" * 60)

        logger.info(
            "Kanyoza AI Platform v14.7 — Shutting down..."
        )

        logger.info("=" * 60)

        if (
            cleanup_task
            and not cleanup_task.done()
        ):
            cleanup_task.cancel()

            try:
                await cleanup_task

            except asyncio.CancelledError:
                pass

            except Exception as exc:
                logger.debug(
                    "Draft cleanup shutdown error: %s",
                    str(exc)[:200],
                )

        graceful_shutdown(container)

        app.state.boot_completed = False

        logger.info(
            "Shutdown complete."
        )


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT HELPERS
# ═══════════════════════════════════════════════════════════════════════════
# ... (existing helpers unchanged) ...


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description=(
            "Multi-tenant social media AI platform "
            "with industry plugins"
        ),
        version="14.7.0",
        lifespan=lifespan,
        docs_url=(
            "/docs"
            if settings.is_development
            else None
        ),
        redoc_url=None,
        openapi_url=(
            "/openapi.json"
            if settings.is_development
            else None
        ),
    )

    app.state.boot_completed = False
    app.state.container = None
    app.state.boot_warnings = []

    _configure_middleware(app)
    _configure_cors(app)

    app.add_middleware(
        StartupGateMiddleware
    )

    app.add_middleware(
        ApiCallCounterMiddleware
    )

    _register_health_endpoints(app)

    _mount_routes(app)

    _mount_event_api(app)  # NEW v14.7

    _mount_director_proxy(app)

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": "14.7.0",
            "platform": "Kanyoza AI",
            "docs": (
                "/docs"
                if settings.is_development
                else None
            ),
            "events_api": "/api/v1/events",
        }

    @app.get("/workspaces")
    async def get_workspaces():
        """Return available workspaces from Supabase."""
        try:
            from services.supabase import (
                get_service_client_from_settings,
            )

            client = (
                get_service_client_from_settings()
            )

            if client:
                result = (
                    client
                    .table("workspaces")
                    .select("*")
                    .execute()
                )

                return {
                    "workspaces": (
                        result.data or []
                    )
                }

        except Exception as exc:
            logger.debug(
                "Workspace lookup failed: %s",
                str(exc)[:200],
            )

        return {
            "workspaces": []
        }

    return app


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING (Updated with Event API + Trend/Backup routes)
# ═══════════════════════════════════════════════════════════════════════════


def _mount_routes(
    app: FastAPI,
) -> None:
    """Mount core application routers."""

    route_modules = [
        (
            "API v1",
            "api.v1",
            "router",
        ),
        (
            "Rule Engine",
            "routes.rule_routes",
            "router",
        ),
        (
            "Meta Engine",
            "routes.meta_routes",
            "router",
        ),
        (
            "Email Service",
            "routes.email_routes",
            "router",
        ),
        (
            "Webhook",
            "routes.webhook",
            "router",
        ),
    ]

    for (
        name,
        module_path,
        attr,
    ) in route_modules:
        try:
            module = __import__(
                module_path,
                fromlist=[attr],
            )

            router = getattr(
                module,
                attr,
                None,
            )

            if router:
                app.include_router(
                    router
                )

                logger.info(
                    "%s routes mounted.",
                    name,
                )

        except ImportError:
            logger.debug(
                "%s routes not available — skipping.",
                name,
            )

        except Exception as exc:
            logger.warning(
                "%s routes failed to mount: %s",
                name,
                str(exc)[:300],
            )

    # ═══════════════════════════════════════════════════════════════
    # MOUNT TREND INTELLIGENCE ROUTES (NEW)
    # ═══════════════════════════════════════════════════════════════
    _mount_trend_routes(app)

    # ═══════════════════════════════════════════════════════════════
    # MOUNT BACKUP ROUTES (NEW)
    # ═══════════════════════════════════════════════════════════════
    _mount_backup_routes(app)

    _mount_plugin(
        app,
        "growthbridge",
        "Growthbridge",
    )

    try:
        from routes.code_intel_routes import (
            router as code_intel_router,
        )

        app.include_router(
            code_intel_router
        )

        logger.info(
            "Code Intelligence routes mounted."
        )

    except ImportError:
        logger.debug(
            "Code Intelligence routes "
            "not available."
        )

    except Exception as exc:
        logger.warning(
            "Code Intelligence routes failed "
            "to mount: %s",
            str(exc)[:300],
        )


def _mount_trend_routes(app: FastAPI) -> None:
    """Mount Trend Intelligence public and admin routes."""
    
    # Public routes
    try:
        from plugins.trend_intelligence.routes_public import (
            router as trend_public_router,
        )
        app.include_router(trend_public_router)
        logger.info("Trend Intelligence public routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence public routes not available.")
    except Exception as exc:
        logger.warning(f"Trend Intelligence public routes mount failed: {str(exc)[:200]}")

    # Admin routes
    try:
        from plugins.trend_intelligence.routes_admin import (
            router as trend_admin_router,
        )
        app.include_router(trend_admin_router)
        logger.info("Trend Intelligence admin routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence admin routes not available.")
    except Exception as exc:
        logger.warning(f"Trend Intelligence admin routes mount failed: {str(exc)[:200]}")


def _mount_backup_routes(app: FastAPI) -> None:
    """Mount Backup public and admin routes."""
    
    # Public routes
    try:
        from plugins.backup.routes_public import (
            router as backup_public_router,
        )
        app.include_router(backup_public_router)
        logger.info("Backup public routes mounted.")
    except ImportError:
        logger.debug("Backup public routes not available.")
    except Exception as exc:
        logger.warning(f"Backup public routes mount failed: {str(exc)[:200]}")

    # Admin routes
    try:
        from plugins.backup.routes_admin import (
            router as backup_admin_router,
        )
        app.include_router(backup_admin_router)
        logger.info("Backup admin routes mounted.")
    except ImportError:
        logger.debug("Backup admin routes not available.")
    except Exception as exc:
        logger.warning(f"Backup admin routes mount failed: {str(exc)[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

fastapi_app = create_app()

app = create_socket_app(
    fastapi_app
)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════

def _get_python_version() -> str:
    return (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
    )
