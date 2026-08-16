"""
Kanyoza Systems AI Platform v14.6 — FastAPI Entry Point.

Orchestrates application creation, middleware, route mounting, and
the phased boot sequence via the lifecycle module.

v14.6 DIRECTOR PROXY:
  - /director/* routes proxy to Director instance
  - Dashboard can call director endpoints from API instance

v14.5 SERVICE_MODE FIX:
  - Removed duplicate service initialization
  - _build_boot_sequence handles ALL services

v14.0 FIXES (retained):
  - _safe_publish() logs EventBus failures
  - _stop_director() logs shutdown failures
"""

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
from fastapi import FastAPI, Request, Response, HTTPException
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

# ── Suppress known library noise ──────────────────────────────────────
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")

# Director service URL for proxy (set in Render env vars)
DIRECTOR_SERVICE_URL = os.getenv(
    "DIRECTOR_SERVICE_URL",
    "https://omnexa-director.onrender.com",
)


def _get_service_mode() -> str:
    """Get SERVICE_MODE from env or settings."""
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
    _boot_warnings.append({
        "service": service,
        "error": str(exc)[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    logger.warning("Boot warning [%s]: %s", service, exc)


# ═══════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════

class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Tracks daily API call counts in Redis."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            try:
                container = getattr(request.app.state, "container", None)
                if container:
                    from core.redis_client import UpstashRedisClient
                    redis = container.try_resolve(UpstashRedisClient)
                    if redis and redis.is_available:
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        key = f"metrics:api_calls:{today}"
                        count = redis.incr(key)
                        if count == 1:
                            redis.expire(key, 86400 * 2)
            except Exception:
                pass
        return await call_next(request)


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Returns 503 until boot is complete."""

    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "boot_completed", False):
            if request.url.path in ("/health", "/health/live", "/health/ready"):
                return await call_next(request)
            return Response(
                content=json.dumps({
                    "status": "starting",
                    "message": "Application is still initializing. Please retry.",
                }),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": "5"},
            )
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# DIRECTOR PROXY (v14.6)
# ═══════════════════════════════════════════════════════════════════════════

def _mount_director_proxy(app: FastAPI) -> None:
    """Proxy /director/* requests to the Director instance.

    v14.6: The dashboard calls Director endpoints from the API instance.
    This proxy forwards those requests to the Director service.
    """

    @app.api_route(
        "/director/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def proxy_director(request: Request, path: str):
        target_url = f"{DIRECTOR_SERVICE_URL}/director/{path}"

        # Forward headers (strip host and content-length)
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        # Forward query params
        params = dict(request.query_params)

        # Forward body
        body = await request.body()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=params,
                    content=body if body else None,
                )

            return Response(
                content=response.content,
                status_code=response.status_code,
                headers={
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ("content-encoding", "transfer-encoding", "connection", "content-length")
                },
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.TimeoutException:
            return Response(
                content=json.dumps({"error": "Director service timed out"}),
                status_code=504,
                media_type="application/json",
            )
        except httpx.ConnectError:
            return Response(
                content=json.dumps({"error": "Director service unavailable"}),
                status_code=503,
                media_type="application/json",
            )
        except Exception as exc:
            logger.error("Director proxy error: %s", exc)
            return Response(
                content=json.dumps({"error": "Proxy error", "detail": str(exc)[:200]}),
                status_code=502,
                media_type="application/json",
            )

    logger.info(
        "Director proxy mounted — forwarding /director/* to %s",
        DIRECTOR_SERVICE_URL,
    )


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_started_at, _startup_completed_at, _startup_duration_seconds

    _startup_started_at = datetime.now(timezone.utc)
    startup_start = time.monotonic()
    service_mode = _get_service_mode()

    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v14.6 — Starting up...")
    logger.info("  Environment : %s", settings.environment)
    logger.info("  Service Mode: %s", service_mode)
    logger.info("  Log level   : %s", settings.log_level)
    logger.info("  Python      : %s", _get_python_version())
    logger.info("=" * 60)

    settings.validate_required()

    logger.info("Building service container...")
    container: ServiceContainer = build_container()
    app.state.container = container

    redis_client = _resolve_redis(container)

    # Boot only what this service mode needs
    try:
        boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")
    except SystemExit:
        raise
    except Exception as exc:
        _record_boot_warning("boot_sequence", exc)

    # API stores (always needed for API modes)
    if service_mode in ("full", "api", ""):
        _init_api_stores(app, redis_client)

    # Draft cleanup (API modes only)
    cleanup_task = None
    if service_mode in ("full", "api", ""):
        cleanup_task = asyncio.create_task(_draft_cleanup_loop(app))
        shutdown_registry.register(
            "DraftCleanup",
            lambda: cleanup_task.cancel(),
            timeout_seconds=5.0,
        )

    app.state.boot_completed = True
    app.state.boot_warnings = list(_boot_warnings)

    _startup_completed_at = datetime.now(timezone.utc)
    _startup_duration_seconds = time.monotonic() - startup_start

    if _boot_warnings:
        logger.warning(
            "Boot completed with %d warning(s) in %.2fs:",
            len(_boot_warnings), _startup_duration_seconds,
        )
        for w in _boot_warnings:
            logger.warning("  ⚠ %s: %s", w["service"], w["error"])
    else:
        logger.info(
            "Boot complete — all services healthy (%.2fs).",
            _startup_duration_seconds,
        )

    logger.info("Application ready to accept requests.")
    logger.info("=" * 60)

    yield

    # ── SHUTDOWN ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Kanyoza AI Platform v14.6 — Shutting down...")
    logger.info("=" * 60)

    if cleanup_task and not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    graceful_shutdown(container)

    logger.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_redis(container: ServiceContainer):
    try:
        from core.redis_client import UpstashRedisClient
        redis = container.try_resolve(UpstashRedisClient)
        if redis:
            logger.info("Redis client resolved for wiring.")
        return redis
    except Exception as exc:
        logger.debug("Redis not available: %s", exc)
        return None


def _init_api_stores(app, redis_client) -> None:
    from api.v1 import DraftStore, AsyncAuditLog, AsyncWorkflowHistory, AsyncKeyStore

    app.state.draft_store = DraftStore(redis_client=redis_client)
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(maxlen=20)
    app.state.key_store = AsyncKeyStore()

    try:
        asyncio.create_task(app.state.audit_log.seed_from_supabase())
    except Exception as exc:
        logger.debug("Audit log seeding skipped: %s", exc)

    logger.info("API stores initialized (DraftStore, AuditLog, WorkflowHistory, KeyStore).")


async def _draft_cleanup_loop(app) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Draft cleanup error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

def _build_health_response(app: FastAPI) -> dict[str, Any]:
    booted = getattr(app.state, "boot_completed", False)

    if not booted:
        return {
            "status": "starting",
            "version": "14.6.0",
            "uptime_seconds": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    service_health = health_registry.check_all()
    statuses = [r.status for r in service_health.values()]

    if any(s == HealthStatus.UNHEALTHY for s in statuses):
        overall = "unhealthy"
    elif any(s == HealthStatus.DEGRADED for s in statuses):
        overall = "degraded"
    elif _boot_warnings:
        overall = "degraded"
    else:
        overall = "healthy"

    uptime = (
        (datetime.now(timezone.utc) - _startup_started_at).total_seconds()
        if _startup_started_at else 0
    )

    return {
        "status": overall,
        "version": "14.6.0",
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(_startup_duration_seconds, 3),
        "boot_warnings": len(_boot_warnings),
        "services": {name: report.to_dict() for name, report in service_health.items()},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description="Multi-tenant social media AI platform with industry plugins",
        version="14.6.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    _configure_middleware(app)
    _configure_cors(app)
    app.add_middleware(StartupGateMiddleware)
    app.add_middleware(ApiCallCounterMiddleware)
    _register_health_endpoints(app)
    _mount_routes(app)
    _mount_director_proxy(app)  # v14.6 — Director proxy

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": "14.6.0",
            "platform": "Kanyoza AI",
            "docs": "/docs" if settings.is_development else None,
        }

    # Workspaces endpoint (fix 404)
    @app.get("/workspaces")
    async def get_workspaces():
        try:
            from services.supabase import get_service_client_from_settings
            client = get_service_client_from_settings()
            if client:
                result = client.table("workspaces").select("*").execute()
                return {"workspaces": result.data or []}
        except Exception:
            pass
        return {"workspaces": []}

    return app


def _configure_middleware(app: FastAPI) -> None:
    from api.v1 import (
        SecurityHeadersMiddleware,
        RequestSizeLimitMiddleware,
        RequestTracingMiddleware,
        CorrelationIDMiddleware,
    )

    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_size=1_048_576)
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    try:
        from middleware.traffic_capture import TrafficCaptureMiddleware
        app.add_middleware(TrafficCaptureMiddleware)
        logger.info("Traffic capture middleware registered.")
    except ImportError:
        logger.debug("Traffic capture middleware not available.")


def _configure_cors(app: FastAPI) -> None:
    if settings.is_production:
        origins = settings.cors_allowed_origins
        if not origins:
            raise RuntimeError(
                "No CORS origins configured for production. "
                "Set ALLOWED_ORIGINS environment variable."
            )
        allow_creds = True
    else:
        origins = ["*"]
        allow_creds = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_creds,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
        max_age=86400,
    )

    logger.info("CORS origins: %s", origins if len(origins) < 5 else f"{origins[:3]}... ({len(origins)} total)")


def _register_health_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    async def liveness_check():
        return {
            "status": "alive",
            "version": "14.6.0",
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - _startup_started_at).total_seconds(), 1
            ) if _startup_started_at else 0,
        }

    @app.get("/health/ready")
    async def readiness_check():
        if not getattr(app.state, "boot_completed", False):
            return Response(
                content=json.dumps({"status": "not_ready", "reason": "Boot in progress"}),
                status_code=503,
                media_type="application/json",
            )

        service_health = health_registry.check_all()
        unhealthy = [name for name, r in service_health.items() if r.status == HealthStatus.UNHEALTHY]

        if unhealthy:
            return Response(
                content=json.dumps({
                    "status": "not_ready",
                    "reason": "Unhealthy services detected",
                    "unhealthy_services": unhealthy,
                }),
                status_code=503,
                media_type="application/json",
            )

        return {"status": "ready", "services_monitored": len(service_health)}

    @app.get("/health/detailed")
    async def detailed_health(request: Request):
        if settings.is_production:
            token = request.headers.get("X-API-Key", "")
            auth_header = request.headers.get("Authorization", "")
            if not token and auth_header.startswith("Bearer "):
                token = auth_header[7:]

            master_token = getattr(settings, "master_api_token", "") or ""
            if not master_token or not token or not hmac.compare_digest(token, master_token):
                raise HTTPException(status_code=401, detail="Authentication required")

        return _build_health_response(app)


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING
# ═══════════════════════════════════════════════════════════════════════════

def _mount_routes(app: FastAPI) -> None:
    route_modules = [
        ("API v1", "api.v1", "router", "include_router"),
        ("Rule Engine", "routes.rule_routes", "router", "include_router"),
        ("Meta Engine", "routes.meta_routes", "router", "include_router"),
        ("Email Service", "routes.email_routes", "router", "include_router"),
        ("Webhook", "routes.webhook", "router", "include_router"),
    ]

    for name, module_path, attr, method in route_modules:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router:
                getattr(app, method)(router)
                logger.info("%s routes mounted.", name)
        except ImportError:
            logger.debug("%s routes not available — skipping.", name)
        except Exception as exc:
            logger.warning("%s routes failed to mount: %s", name, exc)

    _mount_plugin(app, "growthbridge", "Growthbridge")

    try:
        from routes.code_intel_routes import router as code_intel_router
        app.include_router(code_intel_router)
        logger.info("Code Intelligence routes mounted.")
    except ImportError:
        logger.debug("Code Intelligence routes not available.")


def _mount_plugin(app: FastAPI, plugin_name: str, display_name: str) -> None:
    try:
        public_module = __import__(f"plugins.{plugin_name}.routes.public", fromlist=["router"])
        admin_module = __import__(f"plugins.{plugin_name}.routes.admin", fromlist=["router"])
        app.include_router(public_module.router)
        app.include_router(admin_module.router)
        logger.info("%s plugin routes mounted.", display_name)
    except ImportError:
        logger.debug("%s plugin not available — skipping.", display_name)
    except Exception as exc:
        logger.warning("%s mount failed: %s", display_name, exc)


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

fastapi_app = create_app()
app = create_socket_app(fastapi_app)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════

def _get_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


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
