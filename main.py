"""
Kanyoza Systems AI Platform — FastAPI Entry Point.

v14.7.2 FIXES:
  - Director routes mounted DIRECTLY for full/core/director mode
  - No proxy for in-process Director (eliminates 404/502 errors)
  - Proxy ONLY used when SERVICE_MODE=api (Director on separate instance)
  - Added /director/* routes directly from services.director module

v14.7.1 FIXES:
  - Director proxy DISABLED when Director runs in-process
  - Event API mounted
  - JobService health DEGRADED not UNHEALTHY

v14.7.5+ lifecycle/health truth fixes:
  - Mount cp_gap_routes (predictions, quality, topology, condition/health, …)
  - Remove duplicate Timeline/E2E registration
  - Mount monitor plugin routes
"""

from __future__ import annotations

from platform_version import PLATFORM_VERSION

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
from config.logging_config import setup_logging

# Configure application logging before service/container boot so production logs
# stay structured and noisy HTTP client loggers do not flood Render stdout.
setup_logging(settings.log_level)

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
from routes.affiliate_routes import router as _affiliate_router  # route-wiring audit anchor


logger = logging.getLogger("kanyoza.main")

# Suppress known library noise
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")

# Director service URL (only used when SERVICE_MODE=api)
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
                        # Redis client is synchronous; never block the event loop.
                        count = await asyncio.to_thread(redis.incr, key)
                        if count == 1:
                            await asyncio.to_thread(redis.expire, key, 86400 * 2)
            except Exception:
                pass
        return await call_next(request)


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Returns 503 until boot has completed."""

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
# CONFIGURATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _configure_middleware(app: FastAPI) -> None:
    """Register application middleware."""
    from api.v1 import (
        SecurityHeadersMiddleware,
        RequestSizeLimitMiddleware,
        RequestTracingMiddleware,
        CorrelationIDMiddleware,
    )

    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_size=10_485_760)
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Canonical authentication + tenant boundary.
    from middleware.request_context_middleware import RequestContextMiddleware
    app.add_middleware(RequestContextMiddleware)
    logger.info("Canonical RequestContext/Auth middleware registered.")

    try:
        from middleware.traffic_capture import TrafficCaptureMiddleware
        app.add_middleware(TrafficCaptureMiddleware)
        logger.info("Traffic capture middleware registered.")
    except ImportError:
        logger.debug("Traffic capture middleware not available.")


def _configure_cors(app: FastAPI) -> None:
    """Configure CORS according to environment."""
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
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
        max_age=86400,
    )
    logger.info(
        "CORS origins: %s",
        origins if len(origins) < 5 else f"{origins[:3]}... ({len(origins)} total)",
    )


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

def _register_health_endpoints(app: FastAPI) -> None:
    """Register liveness, readiness and detailed health endpoints."""

    @app.get("/health")
    @app.head("/health")
    async def liveness_check():
        return {
            "status": "alive",
            "version": PLATFORM_VERSION,
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - _startup_started_at).total_seconds(), 1
            ) if _startup_started_at else 0,
        }

    @app.get("/health/live")
    @app.head("/health/live")
    async def live_check():
        return {
            "status": "alive",
            "version": PLATFORM_VERSION,
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - _startup_started_at).total_seconds(), 1
            ) if _startup_started_at else 0,
        }

    @app.get("/health/ready")
    @app.head("/health/ready")
    async def readiness_check():
        if not getattr(app.state, "boot_completed", False):
            return Response(
                content=json.dumps({"status": "not_ready", "reason": "Boot in progress"}),
                status_code=503,
                media_type="application/json",
            )
        service_health = await health_registry.check_all()
        unhealthy = [
            name for name, report in service_health.items()
            if report.status == HealthStatus.UNHEALTHY
        ]
        boot_warnings = list(getattr(app.state, "boot_warnings", None) or _boot_warnings)
        degraded = [
            name for name, report in service_health.items()
            if report.status == HealthStatus.DEGRADED
        ]
        if unhealthy or degraded or boot_warnings:
            return Response(
                content=json.dumps({
                    "status": "not_ready",
                    "reason": "Required startup/services are degraded",
                    "unhealthy_services": unhealthy,
                    "degraded_services": degraded,
                    "boot_warnings": boot_warnings,
                }, default=str),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": "10"},
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
        return await _build_health_response(app)


async def _build_health_response(app: FastAPI) -> dict[str, Any]:
    """Build the detailed platform health response."""
    booted = getattr(app.state, "boot_completed", False)
    if not booted:
        return {
            "status": "starting",
            "version": PLATFORM_VERSION,
            "uptime_seconds": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    service_health = await health_registry.check_all()
    statuses = [report.status for report in service_health.values()]

    if any(status == HealthStatus.UNHEALTHY for status in statuses):
        overall = "unhealthy"
    elif any(status == HealthStatus.DEGRADED for status in statuses):
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
        "version": PLATFORM_VERSION,
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(_startup_duration_seconds, 3),
        "boot_warnings": len(_boot_warnings),
        "services": {name: report.to_dict() for name, report in service_health.items()},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EVENT API MOUNTING
# ═══════════════════════════════════════════════════════════════════════════

def _mount_event_api(app: FastAPI) -> None:
    """Mount Event API routes for real-time event observability."""
    try:
        from routes.event_api import register_event_api_routes
        register_event_api_routes(app)
        logger.info("Event API routes mounted (/api/v1/events/*).")
    except ImportError:
        logger.debug("Event API routes not available — skipping.")
    except Exception as exc:
        logger.warning("Event API mount failed: %s", str(exc)[:300])


# ═══════════════════════════════════════════════════════════════════════════
# DIRECTOR ROUTES (Direct Mount for In-Process)
# ═══════════════════════════════════════════════════════════════════════════

def _mount_director_routes_directly(app: FastAPI) -> None:
    """Mount Director routes DIRECTLY from services.director module."""
    try:
        from services.director import create_app as create_director_app
        from services.director import director

        if director is None:
            logger.warning("Director instance is None — routes not mounted")
            return

        if getattr(app.state, "director_routes_mounted", False):
            return
        director_app = create_director_app(director)
        app.mount("/director", director_app)
        app.state.director_routes_mounted = True
        logger.info("✅ Director routes mounted directly at /director/*")

    except ImportError as exc:
        logger.warning(f"Director module not available: {exc}")
    except Exception as exc:
        logger.warning(f"Director route mounting failed: {exc}")


def _mount_director_proxy(app: FastAPI) -> None:
    """Mount /director/* proxy ONLY when Director is on SEPARATE instance."""

    service_mode = _get_service_mode()

    # Director runs in-process — mount routes DIRECTLY
    if service_mode in ("full", "core", "director"):
        logger.info(
            "Director proxy DISABLED — Director runs in-process (SERVICE_MODE=%s)",
            service_mode,
        )
        _mount_director_routes_directly(app)
        return

    # Director on separate instance — use proxy
    logger.info(
        "Director proxy ENABLED: /director/* -> %s (SERVICE_MODE=%s)",
        DIRECTOR_SERVICE_URL,
        service_mode,
    )

    request_hop_by_hop_headers = {
        "host", "content-length", "connection", "transfer-encoding", "accept-encoding",
    }
    response_hop_by_hop_headers = {
        "content-length", "connection", "transfer-encoding", "content-encoding",
    }

    @app.api_route("/director/{path:path}", methods=["GET"], operation_id="director_proxy_get")
    @app.api_route("/director/{path:path}", methods=["POST"], operation_id="director_proxy_post")
    @app.api_route("/director/{path:path}", methods=["PUT"], operation_id="director_proxy_put")
    @app.api_route("/director/{path:path}", methods=["DELETE"], operation_id="director_proxy_delete")
    @app.api_route("/director/{path:path}", methods=["PATCH"], operation_id="director_proxy_patch")
    @app.api_route("/director/{path:path}", methods=["OPTIONS"], operation_id="director_proxy_options")
    @app.api_route("/director/{path:path}", methods=["HEAD"], operation_id="director_proxy_head")
    async def proxy_director(request: Request, path: str):
        target_url = f"{DIRECTOR_SERVICE_URL}/{path}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in request_hop_by_hop_headers
        }
        headers["accept-encoding"] = "identity"
        params = list(request.query_params.multi_items())
        body = await request.body()

        try:
            client = getattr(request.app.state, "director_http_client", None)
            if client is None:
                # Defensive fallback for tests or unusual app construction.
                # The lifespan creates the pooled client in normal API mode.
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0),
                    follow_redirects=False,
                )
                request.app.state.director_http_client = client

            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=params,
                content=body if body else None,
            )

            response_headers: dict[str, str] = {}
            for key, value in upstream.headers.items():
                if key.lower() in response_hop_by_hop_headers:
                    continue
                response_headers[key] = value

            cors_origin = "*"
            try:
                from config.settings import settings as _cors_settings
                if _cors_settings.is_production:
                    allowed = _cors_settings.cors_allowed_origins
                    request_origin = request.headers.get("origin", "")
                    if request_origin and request_origin in allowed:
                        cors_origin = request_origin
                    elif allowed:
                        cors_origin = allowed[0]
            except Exception:
                pass

            response_headers.setdefault("Access-Control-Allow-Origin", cors_origin)
            response_headers.setdefault(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD",
            )
            response_headers.setdefault(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-API-Key, X-Correlation-ID",
            )

            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
            )

        except httpx.TimeoutException:
            logger.warning("Director proxy timeout: %s %s", request.method, request.url.path)
            return Response(
                content=json.dumps({"status": "error", "error": "Director service timeout"}),
                status_code=504,
                media_type="application/json",
            )
        except httpx.ConnectError:
            logger.warning("Director proxy unavailable: %s", DIRECTOR_SERVICE_URL)
            return Response(
                content=json.dumps({"status": "error", "error": "Director service unavailable"}),
                status_code=503,
                media_type="application/json",
            )
        except httpx.RequestError as exc:
            logger.warning("Director proxy request failed: %s", str(exc)[:200])
            return Response(
                content=json.dumps({"status": "error", "error": "Director service request failed"}),
                status_code=502,
                media_type="application/json",
            )
        except Exception:
            logger.exception("Unexpected Director proxy failure: %s", request.url.path)
            return Response(
                content=json.dumps({"status": "error", "error": "Director proxy failure"}),
                status_code=502,
                media_type="application/json",
            )


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING
# ═══════════════════════════════════════════════════════════════════════════

def _mount_routes(app: FastAPI) -> None:
    """Mount core application routers."""
    # KCPG is the operator authority front door; business side effects enter the Action Kernel.
    route_modules = [
        ("API v1", "api.v1", "router"),
        ("Rule Engine", "routes.rule_routes", "router"),
        ("Meta Engine", "routes.meta_routes", "router"),
        ("Email Service", "routes.email_routes", "router"),
        ("Webhook", "routes.webhook", "router"),
        ("Sensor Status", "routes.sensor_routes", "router"),
        ("Affiliate Workload", "routes.affiliate_routes", "router"),
    ]

    for name, module_path, attr in route_modules:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router:
                app.include_router(router)
                logger.info("%s routes mounted.", name)
        except ImportError:
            logger.debug("%s routes not available — skipping.", name)
        except Exception as exc:
            logger.warning("%s routes failed to mount: %s", name, str(exc)[:300])

    try:
        from routes.affiliate_routes import public_router as affiliate_public_router
        app.include_router(affiliate_public_router)
        logger.info("Affiliate public routes mounted.")
    except Exception as exc:
        logger.error("Affiliate public routes failed to mount: %s", str(exc)[:300])

    # Trend Intelligence routes
    try:
        from plugins.trend_intelligence.routes.public import router as trend_public_router
        app.include_router(trend_public_router)
        logger.info("Trend Intelligence public routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence public routes not available.")
    except Exception as exc:
        logger.warning("Trend Intelligence public routes mount failed: %s", str(exc)[:200])

    try:
        from plugins.trend_intelligence.routes.admin import router as trend_admin_router
        app.include_router(trend_admin_router)
        logger.info("Trend Intelligence admin routes mounted.")
    except ImportError:
        logger.debug("Trend Intelligence admin routes not available.")
    except Exception as exc:
        logger.warning("Trend Intelligence admin routes mount failed: %s", str(exc)[:200])

    # Backup routes
    try:
        from plugins.backup.routes.public import router as backup_public_router
        app.include_router(backup_public_router)
        logger.info("Backup public routes mounted.")
    except ImportError:
        logger.debug("Backup public routes not available.")
    except Exception as exc:
        logger.warning("Backup public routes mount failed: %s", str(exc)[:200])

    try:
        from plugins.backup.routes.admin import router as backup_admin_router
        app.include_router(backup_admin_router)
        logger.info("Backup admin routes mounted.")
    except ImportError:
        logger.debug("Backup admin routes not available.")
    except Exception as exc:
        logger.warning("Backup admin routes mount failed: %s", str(exc)[:200])

    # Canonical /api/v1 contract routes — mount ONCE only (no duplicate loop)
    canonical_v1_routes = [
        ("Timeline", "api.timeline_routes", "router"),
        ("E2E", "api.e2e_routes", "router"),
        ("KCPG Action Intent Bridge", "api.action_intent_routes", "router"),
    ]
    for name, module_path, attr in canonical_v1_routes:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router is not None:
                app.include_router(router)
                logger.info("%s routes mounted.", name)
            else:
                logger.error("%s module has no router; refusing silent omission.", name)
        except Exception as exc:
            logger.error("%s routes failed to mount: %s", name, str(exc)[:300])

    control_plane_routes = [
        ("Metabolism", "api.metabolism_routes", "router"),
        ("Organism Body", "api.organism_body_routes", "router"),
        ("Outbox", "api.outbox_routes", "router"),
        ("Reports", "api.report_routes", "router"),
        ("SLO", "api.slo_routes", "router"),
        ("Observability", "api.observability_routes", "router"),
        ("Web Intelligence", "api.web_intelligence", "router"),
        ("Content Intelligence & Business Automation", "api.content_intelligence_automation", "router"),
        ("Unified Health", "api.health_unified", "router"),
        ("Council", "routes.council_routes", "router"),
        ("Circulation", "routes.circulation_routes", "router"),
        ("Pulse", "routes.pulse_routes", "router"),
        ("Production Runtime Convergence", "routes.production_runtime_routes", "router"),
        ("Autonomous Workforce Bridge", "routes.autonomous_workforce_bridge_routes", "router"),
        ("Durable Workforce Execution", "routes.workforce_execution_routes", "router"),
        ("Production Route/Event Wiring", "routes.runtime_wiring_routes", "router"),
        ("Legacy Runtime Authority Fence", "routes.legacy_runtime_fencing_routes", "router"),
        ("Command Center Read Model", "routes.command_center_routes", "router"),
    ]
    for name, module_path, attr in control_plane_routes:
        try:
            module = __import__(module_path, fromlist=[attr])
            router = getattr(module, attr, None)
            if router is not None:
                app.include_router(router)
                # Business Automation owns one deliberately unauthenticated
                # machine-ingress router; all management routes remain behind
                # the canonical auth dependency above.
                if module_path == "api.content_intelligence_automation":
                    public_router = getattr(module, "public_router", None)
                    if public_router is not None:
                        app.include_router(public_router)
                        logger.info("Business Automation public ingress routes mounted.")
                logger.info("%s control-plane routes mounted.", name)
            else:
                logger.error("%s module has no router; refusing silent omission.", name)
        except Exception as exc:
            logger.error("%s control-plane routes failed to mount: %s", name, str(exc)[:300])

    # Complete backend contract routes — authoritative runtime/DB surfaces.
    try:
        from routes.backend_contract_routes import register_backend_contract_routes
        register_backend_contract_routes(app)
        logger.info("Backend contract routes mounted.")
    except Exception as exc:
        logger.error("Backend contract routes failed to mount: %s", str(exc)[:300])

    try:
        from routes.autonomy_routes import register_autonomy_routes
        register_autonomy_routes(app)
        logger.info("Autonomy governance routes mounted.")
    except Exception as exc:
        logger.error("Autonomy routes failed to mount: %s", str(exc)[:300])

    try:
        from routes.cp_surface_routes import register_cp_surface_routes
        register_cp_surface_routes(app)
        logger.info("CP surface routes (former stubs) mounted.")
    except Exception as exc:
        logger.error("CP surface routes failed to mount: %s", str(exc)[:300])

    # Control-plane gap / compatibility routes
    # (predictions, quality, topology, condition/health, plugins, …)
    # Documented in CP_WIRE_ROUTES.md — must be mounted or CP pages 404.
    try:
        from routes.cp_gap_routes import register_cp_gap_routes
        register_cp_gap_routes(app)
        logger.info("CP gap / compatibility routes mounted.")
    except ImportError:
        logger.debug("CP gap routes not available — skipping.")
    except Exception as exc:
        logger.warning("CP gap routes mount failed: %s", str(exc)[:300])

    _mount_plugin(app, "growthbridge", "Growthbridge")
    _mount_plugin(app, "github_connector", "GitHub Connector")
    _mount_plugin(app, "monitor", "Monitor")

    try:
        from routes.code_intel_routes import router as code_intel_router
        app.include_router(code_intel_router)
        logger.info("Code Intelligence routes mounted.")
    except ImportError:
        logger.debug("Code Intelligence routes not available.")
    except Exception as exc:
        logger.warning("Code Intelligence routes failed to mount: %s", str(exc)[:300])


def _mount_plugin(app: FastAPI, plugin_name: str, display_name: str) -> None:
    """Mount public and admin routes for an optional plugin."""
    try:
        public_module = __import__(
            f"plugins.{plugin_name}.routes.public", fromlist=["router"]
        )
        admin_module = __import__(
            f"plugins.{plugin_name}.routes.admin", fromlist=["router"]
        )
        public_router = getattr(public_module, "router", None)
        admin_router = getattr(admin_module, "router", None)

        if public_router:
            app.include_router(public_router)
        if admin_router:
            app.include_router(admin_router)
        logger.info("%s plugin routes mounted.", display_name)
    except ImportError:
        logger.debug("%s plugin not available — skipping.", display_name)
    except Exception as exc:
        logger.warning("%s mount failed: %s", display_name, str(exc)[:300])


# ═══════════════════════════════════════════════════════════════════════════
# POST-BOOT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_redis(container: ServiceContainer):
    """Resolve Redis from the application container."""
    try:
        from core.redis_client import UpstashRedisClient
        redis = container.try_resolve(UpstashRedisClient)
        if redis:
            logger.info("Redis client resolved for wiring.")
        return redis
    except Exception as exp:
        logger.debug("Redis not available: %s", exp)
        return None


def _init_api_stores(app, redis_client) -> None:
    """Initialize API-local stores."""
    from api.v1 import (
        DraftStore, AsyncAuditLog, AsyncWorkflowHistory, AsyncKeyStore,
    )
    app.state.draft_store = DraftStore(redis_client=redis_client)
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(maxlen=20)
    app.state.key_store = AsyncKeyStore()

    try:
        asyncio.create_task(
            app.state.audit_log.seed_from_supabase(),
            name="audit-log-seed",
        )
    except Exception as exc:
        logger.debug("Audit log seeding skipped: %s", exc)

    logger.info("API stores initialized (DraftStore, AuditLog, WorkflowHistory, KeyStore).")


async def _draft_cleanup_loop(app) -> None:
    """Periodically clean expired drafts."""
    while True:
        try:
            await asyncio.sleep(3600)
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Draft cleanup error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_started_at, _startup_completed_at, _startup_duration_seconds

    _startup_started_at = datetime.now(timezone.utc)
    startup_start = time.monotonic()
    service_mode = _get_service_mode()
    _boot_warnings.clear()

    app.state.boot_completed = False
    app.state.boot_warnings = []

    logger.info("=" * 60)
    logger.info(f"Kanyoza AI Platform v{PLATFORM_VERSION} — Starting up...")
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

    # V71.13: compose the Business Operating System + Governed AI Workforce
    # at the production application boundary. This preserves legacy engines
    # while making the V70/V71 runtime an explicit canonical composition.
    try:
        from services.v71_13_production_runtime_convergence import install_production_runtime
        install_production_runtime(app, container)
        logger.info("V71.13 production runtime convergence installed.")
        from services.v71_14_production_route_event_wiring import install_route_event_wiring
        install_route_event_wiring(app, container)
        logger.info("V71.14 production route/event wiring installed.")
        from services.v71_15_legacy_runtime_authority_fencing import install_legacy_runtime_fencing
        install_legacy_runtime_fencing(app, container)
        logger.info("V71.15 legacy runtime authority fencing installed.")
        from services.v71_16_autonomous_loop_workforce_bridge import install_autonomous_loop_workforce_bridge
        install_autonomous_loop_workforce_bridge(app, container)
        logger.info("V71.16 AutonomousLoop workforce bridge installed.")
        from services.v71_17_durable_workforce_execution import install_workforce_execution_coordinator
        install_workforce_execution_coordinator(app, container)
        logger.info("V71.17 durable workforce execution coordinator installed.")
        from services.v71_17_durable_workforce_execution_convergence import install_durable_workforce_execution_convergence
        install_durable_workforce_execution_convergence(app, container)
        logger.info("V71.17 durable workforce execution convergence installed.")
        from services.v71_18_workforce_control_plane_persistence import install_workforce_control_plane_persistence
        install_workforce_control_plane_persistence(app, container)
        logger.info("V71.18 workforce control-plane persistence installed.")
    except Exception as exc:
        _record_boot_warning("v71.13_runtime_convergence", exc)
        raise

    try:
        await boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")
        # Director is instantiated during lifecycle boot. Mount its in-process
        # routes only after that singleton exists; import-time mounting is too early.
        if service_mode in ("full", "core", "director"):
            _mount_director_routes_directly(app)
            app.openapi_schema = None
    except SystemExit:
        raise
    except Exception as exc:
        # Critical infrastructure/core boot failures are fatal.  Do not mark
        # the process ready after a failed boot: StartupGate must remain closed
        # and the ASGI lifespan must fail so the supervisor can restart it.
        _record_boot_warning("boot_sequence", exc)
        app.state.boot_completed = False
        app.state.boot_warnings = list(_boot_warnings)
        raise

    # Initialize Event API after boot
    if service_mode in ("full", "api", ""):
        try:
            from routes.event_api import init_event_api
            await init_event_api()
            logger.info("Event API initialized — capturing live events.")
        except ImportError:
            logger.debug("Event API not available — event capture skipped.")
        except Exception as exc:
            logger.warning("Event API initialization failed: %s", str(exc)[:200])

    # API-local stores
    if service_mode in ("full", "api", ""):
        _init_api_stores(app, redis_client)

    cleanup_task: Optional[asyncio.Task] = None

    if service_mode in ("api", ""):
        app.state.director_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0),
            follow_redirects=False,
        )
        await shutdown_registry.register(
            "DirectorHTTPClient",
            lambda: app.state.director_http_client.aclose()
            if app.state.director_http_client is not None
            else None,
            timeout_seconds=10.0,
        )

        cleanup_task = asyncio.create_task(
            _draft_cleanup_loop(app), name="draft-cleanup-loop"
        )
        await shutdown_registry.register(
            "DraftCleanup",
            lambda: cleanup_task.cancel() if cleanup_task and not cleanup_task.done() else None,
            timeout_seconds=5.0,
        )

    app.state.boot_completed = True
    app.state.boot_warnings = list(_boot_warnings)
    _startup_completed_at = datetime.now(timezone.utc)
    _startup_duration_seconds = time.monotonic() - startup_start

    if _boot_warnings:
        logger.warning(
            "Boot completed with %d warning(s) in %.2fs:",
            len(_boot_warnings),
            _startup_duration_seconds,
        )
        for warning in _boot_warnings:
            logger.warning("  ⚠ %s: %s", warning["service"], warning["error"])
    else:
        logger.info(
            "Boot complete — all configured services initialized (%.2fs).",
            _startup_duration_seconds,
        )

    logger.info("Application ready to accept requests.")
    logger.info("=" * 60)

    try:
        yield
    finally:
        logger.info("=" * 60)
        logger.info(f"Kanyoza AI Platform v{PLATFORM_VERSION} — Shutting down...")
        logger.info("=" * 60)

        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Draft cleanup shutdown error: %s", str(exc)[:200])

        await graceful_shutdown(container)
        app.state.boot_completed = False
        logger.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description="Multi-tenant social media AI platform with industry plugins",
        version=PLATFORM_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    app.state.boot_completed = False
    app.state.container = None
    app.state.boot_warnings = []
    app.state.director_routes_mounted = False
    app.state.director_http_client = None

    _configure_middleware(app)
    _configure_cors(app)

    app.add_middleware(StartupGateMiddleware)
    app.add_middleware(ApiCallCounterMiddleware)

    _register_health_endpoints(app)
    _mount_routes(app)
    _mount_event_api(app)
    _mount_director_proxy(app)  # Smart proxy — direct mount for full mode

    @app.get("/")
    @app.head("/")
    async def root():
        return {
            "status": "ok",
            "version": PLATFORM_VERSION,
            "platform": "Kanyoza AI",
            "docs": "/docs" if settings.is_development else None,
            "events_api": "/api/v1/events",
            "director": "/director",
        }

    return app


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
