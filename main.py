"""
Kanyoza Systems AI Platform — FastAPI Entry Point
Refactored v15.4.1 — route integrity, lifecycle hardening, review fixes.

v15.4.1 changes over v15.4:
- Health auth: 503 when master_api_token is unset; X-API-Key and Bearer are
  checked independently.
- Required-route validation runs at the end of _mount_routes (fails during
  create_app(), with boot warnings populated).
- CORS: full allow-header list; origins accepts list or comma-separated string.
- service_mode frozen once in create_app(); lifespan reads app.state.
- Director proxy client created only when proxy mode is actually mounted, and
  closed once (via shutdown_registry).
- Draft cleanup loop runs whenever the draft store exists (previously only
  api mode, so "full" mode never purged expired drafts).
- Audit-log seed task is cancelled on shutdown and on boot failure.
- Optional empty routers recorded as status="empty" (DEBUG log), not skipped.
- /health/version, skipped_modules, bounded route inventory.
- Boot warnings carry a `critical` flag; app.state is the single source of
  truth when an app is supplied.
- API counter tasks capture the container at dispatch time.
- Affiliate import-time audit anchor no longer able to kill the process.

Required route modules: api.v1 and routes.species_routes.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
import uuid
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from platform_version import PLATFORM_VERSION
from config.settings import settings
from config.logging_config import setup_logging

setup_logging(settings.log_level)

# ---------------------------------------------------------------------------
# LogBuffer bridge. The handler is installed once per Python process.
# ---------------------------------------------------------------------------

try:
    from core.log_buffer import LogBuffer
except Exception:  # pragma: no cover
    LogBuffer = None  # type: ignore[assignment]

if LogBuffer is not None:
    class _LogBufferHandler(logging.Handler):
        """Bridge INFO+ Python log records into the platform LogBuffer."""

        _kanyoza_log_buffer_handler = True

        def emit(self, record: logging.LogRecord) -> None:
            try:
                if record.levelno < logging.INFO:
                    return
                message = record.getMessage()
                if record.name == "kanyoza.main" and "LogBuffer" in message:
                    return

                if record.levelno >= logging.ERROR:
                    level = "ERROR"
                elif record.levelno >= logging.WARNING:
                    level = "WARNING"
                else:
                    level = "INFO"

                LogBuffer.get_instance().add(
                    level,
                    record.name,
                    message[:2000],
                    getattr(record, "correlation_id", None),
                )
            except Exception:
                # Logging must never become an application failure path.
                self.handleError(record)

    _root_logger = logging.getLogger()
    if not any(
        getattr(handler, "_kanyoza_log_buffer_handler", False)
        for handler in _root_logger.handlers
    ):
        _handler = _LogBufferHandler()
        _handler.setLevel(logging.INFO)
        _root_logger.addHandler(_handler)

from service_container import ServiceContainer
from dependency_registry import build_container
from lifecycle import (
    boot_infrastructure,
    graceful_shutdown,
    health_registry,
    shutdown_registry,
    HealthStatus,
)

try:
    from core.redaction import sanitize_error_message as _sanitize_error_message
except ImportError:  # pragma: no cover
    def _sanitize_error_message(exc: Any, max_len: int = 200) -> str:
        return str(exc)[:max_len]


from core.socket_server import create_socket_app

# Import-time audit anchor. Guarded so a failure in the affiliate module cannot
# take down the whole process; the router is mounted (and failure recorded)
# through the route registry below.
try:
    from routes.affiliate_routes import router as _affiliate_router  # noqa: F401
except Exception as _affiliate_exc:  # pragma: no cover
    _affiliate_router = None  # type: ignore[assignment]
    logging.getLogger("kanyoza.main").warning(
        "Affiliate router import-time anchor failed: %s", _affiliate_exc
    )

logger = logging.getLogger("kanyoza.main")
logging.getLogger("supabase_auth").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*_refresh_token_timer.*")

DIRECTOR_SERVICE_URL = os.getenv(
    "DIRECTOR_SERVICE_URL",
    "https://omnexa-director.onrender.com",
).strip().rstrip("/")

MAX_INVENTORY = 2000

CORS_ALLOW_HEADERS = [
    "Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID",
    "X-Request-ID", "X-Workspace-ID", "traceparent", "tracestate",
]

# LEGACY: process-level mirrors kept only for callers that read them without an
# app in scope. App state is authoritative; these are written to only when
# _record_boot_warning() is called without an app. Deprecation target: v16.
_startup_started_at: Optional[datetime] = None
_startup_completed_at: Optional[datetime] = None
_startup_duration_seconds: float = 0.0
_boot_warnings: list[dict[str, Any]] = []


def _get_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _get_service_mode() -> str:
    mode = os.getenv("SERVICE_MODE", "").strip().lower()
    return mode or getattr(settings, "service_mode", "full")


def _sanitize(exc: Any, max_len: int = 500) -> str:
    try:
        return _sanitize_error_message(exc, max_len=max_len)
    except TypeError:
        return str(_sanitize_error_message(exc))[:max_len]
    except Exception:
        return "An internal error occurred."


def _record_boot_warning(
    service: str,
    exc: Exception,
    app: Optional[FastAPI] = None,
    *,
    critical: bool = False,
) -> None:
    """Record a sanitized warning. With an app, app.state is the only store."""
    warning = {
        "service": service,
        "error": _sanitize(exc, 500),
        "critical": critical,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if app is not None:
        warnings_list = getattr(app.state, "boot_warnings", None)
        if warnings_list is None:
            warnings_list = []
            app.state.boot_warnings = warnings_list
        warnings_list.append(warning)
    else:
        _boot_warnings.append(warning)  # LEGACY path
    logger.warning("Boot warning [%s]: %s", service, warning["error"])


# ===========================================================================
# ROUTE REGISTRY
# ===========================================================================

ROUTE_MODULES = [
    # Required platform contracts.
    ("API v1", "api.v1", "router", True),
    ("Species Control Plane", "routes.species_routes", "router", True),

    # Existing application surfaces.
    ("KDO Engineering Console", "routes.kdo_engineering_console", "router", False),
    ("Rule Engine", "routes.rule_routes", "router", False),
    ("Meta Engine", "routes.meta_routes", "router", False),
    ("Email Service", "routes.email_routes", "router", False),
    ("Webhook", "routes.webhook", "router", False),
    ("Sensor Status", "routes.sensor_routes", "router", False),
    ("Affiliate Workload", "routes.affiliate_routes", "router", False),
]

CANONICAL_V1_ROUTES = [
    ("Timeline", "api.timeline_routes", "router"),
    ("E2E", "api.e2e_routes", "router"),
    ("KCPG Action Intent Bridge", "api.action_intent_routes", "router"),
]

CONTROL_PLANE_ROUTES = [
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
    ("Failure Recovery", "routes.failure_recovery_routes", "router"),
    ("Connector Fabric", "routes.connector_fabric_routes", "router"),
    ("Sector Pack Portfolio", "routes.sector_pack_routes", "router"),
    ("Business Onboarding", "routes.business_onboarding_routes", "router"),
    ("Website Integrations", "api.website_integrations", "router"),
    ("AI Control Plane", "routes.ai_control_plane_routes", "router"),
]


def _route_methods(route: Any) -> list[str]:
    """Methods excluding HEAD/OPTIONS; falls back to all so HEAD-only routes
    are not reported with an empty (looks-dead) method list."""
    all_methods = getattr(route, "methods", None) or []
    methods = sorted(m for m in all_methods if m not in {"HEAD", "OPTIONS"})
    return methods or sorted(all_methods)


def _describe_routes(routes: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "path": getattr(route, "path"),
            "methods": _route_methods(route),
            "name": getattr(route, "name", None),
        }
        for route in routes
        if getattr(route, "path", None)
    ]


def _route_inventory(app: FastAPI) -> list[dict[str, Any]]:
    """Return a stable, JSON-safe inventory of registered HTTP routes."""
    inventory: list[dict[str, Any]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        inventory.append({
            "path": path,
            "methods": _route_methods(route),
            "name": getattr(route, "name", None),
            "route_type": type(route).__name__,
        })
    return inventory


def _record_route_result(
    app: FastAPI,
    *,
    name: str,
    module: str,
    required: bool,
    status: str,
    imported: bool = False,
    mounted: bool = False,
    routes_added: Optional[list[dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> None:
    record = {
        "name": name,
        "module": module,
        "required": required,
        "status": status,
        "imported": imported,
        "mounted": mounted,
        "route_count": len(routes_added or []),
        "routes": routes_added or [],
        "error": error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    app.state.route_registry.append(record)
    if status == "failed":
        app.state.route_registration_failures.append(record)


def _mount_module_router(
    app: FastAPI,
    name: str,
    module_path: str,
    attr: str = "router",
    *,
    required: bool = False,
) -> bool:
    """Import and mount a router while recording its actual route footprint."""
    before_ids = {id(route) for route in app.routes}
    try:
        module = __import__(module_path, fromlist=[attr])
        router = getattr(module, attr, None)
        if router is None:
            raise AttributeError(f"{module_path} does not export {attr!r}")
        app.include_router(router)
        added = [route for route in app.routes if id(route) not in before_ids]
        route_details = _describe_routes(added)

        if not route_details:
            if required:
                raise RuntimeError(
                    f"Required router {module_path} registered no endpoints"
                )
            _record_route_result(
                app, name=name, module=module_path, required=False,
                status="empty", imported=True, mounted=True,
            )
            logger.debug("%s mounted with 0 inspectable routes", name)
            return True

        _record_route_result(
            app, name=name, module=module_path, required=required,
            status="verified", imported=True, mounted=True,
            routes_added=route_details,
        )
        logger.info(
            "route.mounted",
            extra={
                "event": "route.mounted",
                "route_name": name,
                "route_module": module_path,
                "route_count": len(route_details),
                "route_prefix": getattr(router, "prefix", ""),
            },
        )
        return True
    except Exception as exc:
        status = "failed" if required else "skipped"
        _record_route_result(
            app, name=name, module=module_path, required=required,
            status=status, error=_sanitize(exc, 300),
        )
        if required:
            logger.error("REQUIRED route module %s failed: %s", module_path, _sanitize(exc, 300))
        elif isinstance(exc, ImportError):
            logger.debug("%s routes unavailable: %s", name, _sanitize(exc, 200))
        else:
            logger.warning("%s routes failed to mount: %s", name, _sanitize(exc, 300))
        return False


def _mount_registration_function(
    app: FastAPI,
    name: str,
    module_path: str,
    function_name: str,
    *,
    required: bool = False,
) -> bool:
    before_ids = {id(route) for route in app.routes}
    try:
        module = __import__(module_path, fromlist=[function_name])
        register = getattr(module, function_name, None)
        if not callable(register):
            raise AttributeError(f"{module_path} does not export callable {function_name!r}")
        register(app)
        added = [route for route in app.routes if id(route) not in before_ids]
        details = _describe_routes(added)
        if not details:
            raise RuntimeError("Registration function added no inspectable HTTP endpoints")
        _record_route_result(
            app, name=name, module=module_path, required=required,
            status="verified", imported=True, mounted=True, routes_added=details,
        )
        logger.info("%s registered (%d endpoints).", name, len(details))
        return True
    except Exception as exc:
        _record_route_result(
            app, name=name, module=module_path, required=required,
            status="failed" if required else "skipped",
            error=_sanitize(exc, 300),
        )
        if required:
            logger.error("REQUIRED registration %s failed: %s", name, _sanitize(exc, 300))
        else:
            logger.warning("%s registration unavailable: %s", name, _sanitize(exc, 300))
        return False


def _validate_route_manifest(app: FastAPI) -> None:
    """Fail during create_app() if any required route module is not verified."""
    failures = [
        item for item in app.state.route_registry
        if item.get("required") and item.get("status") != "verified"
    ]
    if not failures:
        return
    for item in failures:
        _record_boot_warning(
            f"route:{item['module']}",
            RuntimeError(f"Required route failed: {item.get('error') or item['status']}"),
            app,
            critical=True,
        )
    raise RuntimeError(
        "Required route registration failed: "
        + "; ".join(
            f"{item['name']} ({item['module']}): "
            f"{item.get('error') or item['status']}"
            for item in failures
        )
    )


def _mount_routes(app: FastAPI) -> None:
    """Mount all application routes through the central registry."""
    for name, module_path, attr, required in ROUTE_MODULES:
        _mount_module_router(app, name, module_path, attr, required=required)

    # Affiliate public ingress is a distinct router from the private router.
    try:
        from routes.affiliate_routes import public_router as affiliate_public_router
        before = {id(route) for route in app.routes}
        app.include_router(affiliate_public_router)
        details = _describe_routes(
            r for r in app.routes if id(r) not in before
        )
        _record_route_result(
            app, name="Affiliate Public", module="routes.affiliate_routes",
            required=False, status="verified" if details else "skipped",
            imported=True, mounted=bool(details), routes_added=details,
            error=None if details else "No inspectable endpoints registered",
        )
    except Exception as exc:
        _record_route_result(
            app, name="Affiliate Public", module="routes.affiliate_routes",
            required=False, status="skipped", error=_sanitize(exc, 300),
        )

    optional_routers = [
        ("Trend Intelligence Public", "plugins.trend_intelligence.routes.public", "router"),
        ("Trend Intelligence Admin", "plugins.trend_intelligence.routes.admin", "router"),
        ("Backup Public", "plugins.backup.routes.public", "router"),
        ("Backup Admin", "plugins.backup.routes.admin", "router"),
    ]
    for name, module_path, attr in optional_routers:
        _mount_module_router(app, name, module_path, attr)

    for name, module_path, attr in CANONICAL_V1_ROUTES:
        _mount_module_router(app, name, module_path, attr)

    for name, module_path, attr in CONTROL_PLANE_ROUTES:
        mounted = _mount_module_router(app, name, module_path, attr)
        if mounted and module_path == "api.content_intelligence_automation":
            try:
                module = __import__(module_path, fromlist=["public_router"])
                public_router = getattr(module, "public_router", None)
                if public_router is not None:
                    before = {id(r) for r in app.routes}
                    app.include_router(public_router)
                    details = _describe_routes(
                        r for r in app.routes if id(r) not in before
                    )
                    _record_route_result(
                        app, name="Business Automation Public",
                        module=module_path, required=False,
                        status="verified" if details else "skipped",
                        imported=True, mounted=bool(details), routes_added=details,
                    )
            except Exception as exc:
                _record_route_result(
                    app, name="Business Automation Public", module=module_path,
                    required=False, status="skipped", error=_sanitize(exc, 300),
                )

    _mount_registration_function(
        app, "Backend Contract Routes", "routes.backend_contract_routes",
        "register_backend_contract_routes",
    )
    _mount_registration_function(
        app, "Autonomy Governance Routes", "routes.autonomy_routes",
        "register_autonomy_routes",
    )
    _mount_registration_function(
        app, "CP Surface Routes", "routes.cp_surface_routes",
        "register_cp_surface_routes",
    )
    _mount_registration_function(
        app, "CP Gap Compatibility Routes", "routes.cp_gap_routes",
        "register_cp_gap_routes",
    )

    _mount_plugin(app, "growthbridge", "Growthbridge")
    _mount_plugin(app, "github_connector", "GitHub Connector")
    _mount_plugin(app, "monitor", "Monitor")
    _mount_module_router(app, "Code Intelligence", "routes.code_intel_routes")

    _validate_route_manifest(app)


def _mount_plugin(app: FastAPI, plugin_name: str, display_name: str) -> None:
    """Mount public/admin plugin routers independently so one cannot hide the other."""
    for surface in ("public", "admin"):
        module_path = f"plugins.{plugin_name}.routes.{surface}"
        _mount_module_router(
            app, f"{display_name} {surface.title()}", module_path, "router"
        )


# ===========================================================================
# MIDDLEWARE AND CORS
# ===========================================================================

class ApiCallCounterMiddleware(BaseHTTPMiddleware):
    """Bounded fire-and-forget API counter; never waits for Redis on request path."""

    MAX_PENDING = int(os.getenv("API_COUNTER_MAX_PENDING", "500"))

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            container = getattr(request.app.state, "container", None)
            if container is not None:
                pending = getattr(request.app.state, "api_counter_tasks", None)
                if pending is None:
                    pending = set()
                    request.app.state.api_counter_tasks = pending
                if len(pending) < self.MAX_PENDING:
                    try:
                        # The task holds its own container reference so it is
                        # independent of later app.state.container changes.
                        task = asyncio.create_task(self._count(container))
                        pending.add(task)
                        task.add_done_callback(pending.discard)
                    except RuntimeError:
                        pass
                else:
                    request.app.state.api_counter_dropped = (
                        getattr(request.app.state, "api_counter_dropped", 0) + 1
                    )
        return await call_next(request)

    @staticmethod
    async def _count(container: ServiceContainer) -> None:
        try:
            from core.redis_client import UpstashRedisClient
            redis = container.try_resolve(UpstashRedisClient)
            if not redis or not getattr(redis, "is_available", False):
                return
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = f"metrics:api_calls:{today}"
            count = await asyncio.to_thread(redis.incr, key)
            if count == 1:
                await asyncio.to_thread(redis.expire, key, 172800)
        except Exception:
            logger.debug("API call counter failed", exc_info=True)


class StartupGateMiddleware(BaseHTTPMiddleware):
    _EXEMPT_PATHS = {
        "/health", "/health/live", "/health/ready",
        "/health/detailed", "/health/routes", "/health/version",
    }

    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "boot_completed", False):
            if request.url.path in self._EXEMPT_PATHS:
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


def _configure_middleware(app: FastAPI) -> None:
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

    from middleware.request_context_middleware import RequestContextMiddleware
    app.add_middleware(RequestContextMiddleware)
    logger.info("Canonical RequestContext/Auth middleware registered.")

    try:
        from middleware.traffic_capture import TrafficCaptureMiddleware
        app.add_middleware(TrafficCaptureMiddleware)
        logger.info("Traffic capture middleware registered.")
    except ImportError:
        logger.debug("Traffic capture middleware unavailable.")


def _parse_origins(raw: Any) -> list[str]:
    """Accept a list/tuple/set or a comma-separated string of origins."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [str(o).strip() for o in raw if str(o).strip()]


def _configure_cors(app: FastAPI) -> None:
    if settings.is_production:
        origins = _parse_origins(getattr(settings, "cors_allowed_origins", None))
        if not origins:
            raise RuntimeError(
                "No CORS origins configured for production. "
                "Set ALLOWED_ORIGINS environment variable."
            )
        allow_credentials = True
    else:
        origins = ["*"]
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        allow_headers=CORS_ALLOW_HEADERS,
        expose_headers=["X-Correlation-ID"],
        max_age=86400,
    )
    logger.info(
        "CORS configured: %s",
        origins if len(origins) < 5 else f"{origins[:3]}... ({len(origins)} total)",
    )


# ===========================================================================
# HEALTH ENDPOINTS
# ===========================================================================

def _boot_meta(app: FastAPI) -> dict[str, Any]:
    return {
        "boot_id": getattr(app.state, "boot_id", None),
        "service_mode": getattr(app.state, "service_mode", None),
        "python_version": getattr(app.state, "python_version", None),
    }


def _production_health_auth(request: Request) -> None:
    if not settings.is_production:
        return
    master_token = getattr(settings, "master_api_token", "") or ""
    if not master_token:
        # Server misconfiguration, not a client credential problem.
        raise HTTPException(status_code=503, detail="master_api_token_not_configured")

    api_key = request.headers.get("X-API-Key", "").strip()
    bearer = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()

    # Checked independently so a stale X-API-Key cannot mask a valid Bearer.
    api_key_ok = bool(api_key) and hmac.compare_digest(api_key, master_token)
    bearer_ok = bool(bearer) and hmac.compare_digest(bearer, master_token)
    if not (api_key_ok or bearer_ok):
        raise HTTPException(status_code=401, detail="Authentication required")


def _register_health_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    @app.head("/health")
    async def liveness_check():
        started = getattr(app.state, "startup_started_at", None)
        uptime = (datetime.now(timezone.utc) - started).total_seconds() if started else 0
        return {
            "status": "alive",
            "version": PLATFORM_VERSION,
            "uptime_seconds": round(uptime, 1),
            **_boot_meta(app),
        }

    @app.get("/health/live")
    @app.head("/health/live")
    async def live_check():
        return await liveness_check()

    @app.get("/health/version")
    async def version_check():
        return {
            "version": PLATFORM_VERSION,
            "commit": (os.getenv("RENDER_GIT_COMMIT") or "unknown")[:12],
            "deployed_at": os.getenv("RENDER_DEPLOY_CREATED_AT"),
            "boot_id": getattr(app.state, "boot_id", None),
            "python_version": getattr(app.state, "python_version", None),
        }

    @app.get("/health/routes")
    async def route_health(request: Request):
        _production_health_auth(request)
        inventory = _route_inventory(app)
        records = list(app.state.route_registry)
        required_failures = [
            item for item in records
            if item.get("required") and item.get("status") != "verified"
        ]
        skipped = [item for item in records if item.get("status") == "skipped"]
        empty = [item for item in records if item.get("status") == "empty"]
        return {
            "status": "healthy" if not required_failures else "unhealthy",
            "registered_endpoint_count": len(inventory),
            "route_module_count": len(records),
            "required_route_failures": required_failures,
            "registration_failures": list(app.state.route_registration_failures),
            "skipped_modules": skipped,
            "empty_modules": empty,
            "modules": records,
            "routes": inventory[:MAX_INVENTORY],
            "routes_total": len(inventory),
            "routes_truncated": len(inventory) > MAX_INVENTORY,
            **_boot_meta(app),
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

        route_failures = [
            item for item in app.state.route_registry
            if item.get("required") and item.get("status") != "verified"
        ]

        try:
            snapshot = health_registry.read_snapshot()
        except Exception:
            snapshot = {}

        if not snapshot:
            try:
                snapshot = await health_registry.check_all()
            except Exception as exc:
                logger.warning("Readiness registry probe failed: %s", _sanitize(exc, 300))
                snapshot = {}

        unhealthy = [
            name for name, report in snapshot.items()
            if report.status == HealthStatus.UNHEALTHY
        ]
        degraded = [
            name for name, report in snapshot.items()
            if report.status == HealthStatus.DEGRADED
        ]
        unknown = [
            name for name, report in snapshot.items()
            if report.status == HealthStatus.UNKNOWN
        ]
        boot_warnings = list(app.state.boot_warnings)
        has_critical = any(w.get("critical") for w in boot_warnings)
        blockers = bool(route_failures or unhealthy or degraded or boot_warnings)

        payload = {
            "status": "not_ready" if blockers else "ready",
            "reason": (
                "Required routes, startup warnings, or service health checks failed"
                if blockers else None
            ),
            "required_route_failures": route_failures,
            "unhealthy_services": unhealthy,
            "degraded_services": degraded,
            "unknown_services": unknown,
            "boot_warnings": boot_warnings,
            "services_monitored": len(snapshot),
            **_boot_meta(app),
        }
        retry_after = "60" if (has_critical or route_failures) else "10"
        return Response(
            content=json.dumps(payload, default=str),
            status_code=503 if blockers else 200,
            media_type="application/json",
            headers={"Retry-After": retry_after} if blockers else {},
        )

    @app.get("/health/detailed")
    async def detailed_health(request: Request):
        _production_health_auth(request)
        return await _build_health_response(app)


async def _build_health_response(app: FastAPI) -> dict[str, Any]:
    booted = getattr(app.state, "boot_completed", False)
    if not booted:
        return {
            "status": "starting",
            "version": PLATFORM_VERSION,
            "uptime_seconds": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            **_boot_meta(app),
        }

    try:
        service_health = health_registry.read_snapshot()
    except Exception:
        service_health = {}
    if not service_health:
        try:
            service_health = await health_registry.check_all()
        except Exception as exc:
            logger.warning("Detailed health probe failed: %s", _sanitize(exc, 300))
            service_health = {}

    statuses = [report.status for report in service_health.values()]
    route_failures = [
        item for item in app.state.route_registry
        if item.get("required") and item.get("status") != "verified"
    ]
    if route_failures or any(s == HealthStatus.UNHEALTHY for s in statuses):
        overall = "unhealthy"
    elif (
        app.state.boot_warnings
        or any(s in (HealthStatus.DEGRADED, HealthStatus.UNKNOWN) for s in statuses)
    ):
        overall = "degraded"
    else:
        overall = "healthy"

    started = getattr(app.state, "startup_started_at", None)
    uptime = (datetime.now(timezone.utc) - started).total_seconds() if started else 0
    snapshot_age = None
    try:
        reader = getattr(health_registry, "read_snapshot_age_seconds", None)
        if callable(reader):
            snapshot_age = reader()
    except Exception:
        pass

    return {
        "status": overall,
        "version": PLATFORM_VERSION,
        "uptime_seconds": round(uptime, 1),
        "boot_duration_seconds": round(app.state.startup_duration_seconds, 3),
        "boot_warnings": len(app.state.boot_warnings),
        "critical_boot_warnings": sum(
            1 for w in app.state.boot_warnings if w.get("critical")
        ),
        "required_route_failures": route_failures,
        "route_modules_registered": sum(
            1 for item in app.state.route_registry if item.get("status") == "verified"
        ),
        "registered_endpoint_count": len(_route_inventory(app)),
        "health_snapshot_age_seconds": (
            round(snapshot_age, 2) if snapshot_age is not None else None
        ),
        "services": {name: report.to_dict() for name, report in service_health.items()},
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **_boot_meta(app),
    }


# ===========================================================================
# EVENT API AND DIRECTOR
# ===========================================================================

def _mount_event_api(app: FastAPI) -> None:
    try:
        from routes.event_api import register_event_api_routes
        before = {id(r) for r in app.routes}
        register_event_api_routes(app)
        details = _describe_routes(r for r in app.routes if id(r) not in before)
        _record_route_result(
            app, name="Event API", module="routes.event_api", required=False,
            status="verified" if details else "skipped",
            imported=True, mounted=bool(details), routes_added=details,
            error=None if details else "Registration added no inspectable endpoints",
        )
    except ImportError as exc:
        _record_route_result(
            app, name="Event API", module="routes.event_api", required=False,
            status="skipped", error=_sanitize(exc, 300),
        )
    except Exception as exc:
        _record_route_result(
            app, name="Event API", module="routes.event_api", required=False,
            status="failed", error=_sanitize(exc, 300),
        )
        logger.warning("Event API mount failed: %s", _sanitize(exc, 300))


async def _mount_director_routes_directly(app: FastAPI) -> bool:
    """Await the live Director instance and mount its ASGI app exactly once."""
    if getattr(app.state, "director_routes_mounted", False):
        return True
    try:
        from services.director import create_app as create_director_app
        from services.director import get_director
        director = await get_director()
        if director is None:
            raise RuntimeError("Director accessor returned None after infrastructure boot")
        director_app = create_director_app(director)
        app.mount("/director", director_app)
        app.state.director_routes_mounted = True
        logger.info("Director routes mounted directly at /director/*")
        return True
    except Exception as exc:
        _record_boot_warning("director_route_mount", exc, app, critical=True)
        logger.exception("Director route mounting failed")
        return False


def _mount_director_proxy(app: FastAPI) -> None:
    service_mode = app.state.service_mode
    if service_mode in ("full", "core", "director"):
        logger.info(
            "Director proxy disabled; Director is in-process (SERVICE_MODE=%s)",
            service_mode,
        )
        app.state.director_mount_mode = "in_process"
        return

    app.state.director_mount_mode = "proxy"
    logger.info("Director proxy enabled: /director/* -> %s", DIRECTOR_SERVICE_URL)

    request_hop = {
        "host", "content-length", "connection", "transfer-encoding", "accept-encoding",
    }
    response_hop = {
        "content-length", "connection", "transfer-encoding", "content-encoding",
    }

    @app.api_route(
        "/director/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        operation_id="director_proxy",
    )
    async def proxy_director(request: Request, path: str):
        target_url = f"{DIRECTOR_SERVICE_URL}/{path}"
        headers = {
            key: value for key, value in request.headers.items()
            if key.lower() not in request_hop
        }
        headers["accept-encoding"] = "identity"
        try:
            client = getattr(request.app.state, "director_http_client", None)
            if client is None:
                raise RuntimeError("Director proxy HTTP client was not initialized")
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=list(request.query_params.multi_items()),
                content=await request.body() or None,
            )
            response_headers = {
                key: value for key, value in upstream.headers.items()
                if key.lower() not in response_hop
            }
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
            )
        except httpx.TimeoutException:
            logger.warning("Director proxy timeout: %s %s", request.method, request.url.path)
            return Response(
                content='{"status":"error","error":"Director service timeout"}',
                status_code=504, media_type="application/json",
            )
        except httpx.ConnectError:
            logger.warning("Director proxy unavailable: %s", DIRECTOR_SERVICE_URL)
            return Response(
                content='{"status":"error","error":"Director service unavailable"}',
                status_code=503, media_type="application/json",
            )
        except httpx.RequestError as exc:
            logger.warning("Director proxy request failed: %s", _sanitize(exc, 200))
            return Response(
                content='{"status":"error","error":"Director service request failed"}',
                status_code=502, media_type="application/json",
            )
        except Exception:
            logger.exception("Unexpected Director proxy failure: %s", request.url.path)
            return Response(
                content='{"status":"error","error":"Director proxy failure"}',
                status_code=502, media_type="application/json",
            )


# ===========================================================================
# POST-BOOT HELPERS
# ===========================================================================

def _resolve_redis(container: ServiceContainer):
    try:
        from core.redis_client import UpstashRedisClient
        redis = container.try_resolve(UpstashRedisClient)
        if redis:
            logger.info("Redis client resolved for wiring.")
        return redis
    except Exception as exc:
        logger.debug("Redis unavailable: %s", _sanitize(exc, 200))
        return None


def _init_api_stores(app: FastAPI, redis_client) -> None:
    from api.v1_stores import DraftStore, AsyncAuditLog, AsyncWorkflowHistory, AsyncKeyStore

    app.state.draft_store = DraftStore(redis_client=redis_client)
    app.state.audit_log = AsyncAuditLog(maxlen=200)
    app.state.workflow_history = AsyncWorkflowHistory(maxlen=20)
    app.state.key_store = AsyncKeyStore()

    async def seed_and_log() -> None:
        try:
            await app.state.audit_log.seed_from_supabase()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Audit-log seed failed")

    try:
        app.state.audit_log_seed_task = asyncio.create_task(
            seed_and_log(), name="audit-log-seed"
        )
    except Exception as exc:
        logger.warning("Audit-log seed task could not be scheduled: %s", _sanitize(exc, 200))
    logger.info("API stores initialized.")


async def _draft_cleanup_loop(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.sleep(3600)
            await app.state.draft_store.cleanup_expired()
            logger.debug("Draft cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Draft cleanup error: %s", _sanitize(exc, 200))


async def _cancel_task(task: Optional[asyncio.Task], label: str) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("%s task shutdown error: %s", label, _sanitize(exc, 200))


async def _best_effort_shutdown(container: Optional[ServiceContainer]) -> None:
    if container is None:
        return
    try:
        await asyncio.wait_for(graceful_shutdown(container), timeout=15.0)
    except Exception:
        logger.exception("Best-effort partial shutdown failed")


# ===========================================================================
# LIFESPAN
# ===========================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_started_at, _startup_completed_at, _startup_duration_seconds

    started_at = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    _startup_started_at = started_at
    # service_mode is frozen in create_app(); never re-read the environment.
    service_mode = app.state.service_mode

    app.state.startup_started_at = started_at
    app.state.startup_completed_at = None
    app.state.startup_duration_seconds = 0.0
    app.state.python_version = _get_python_version()
    app.state.boot_id = uuid.uuid4().hex
    app.state.boot_completed = False
    app.state.boot_warnings = []
    app.state.container = None
    app.state.director_http_client = None

    # Reset the LEGACY module mirror for a single-app process.
    _boot_warnings.clear()

    logger.info(
        "app.boot.start",
        extra={
            "event": "app.boot.start",
            "boot_id": app.state.boot_id,
            "version": PLATFORM_VERSION,
            "environment": settings.environment,
            "service_mode": service_mode,
            "python": app.state.python_version,
            "log_level": settings.log_level,
        },
    )

    # Required-route validation already ran in create_app() via
    # _validate_route_manifest(); a failure there prevents the app from existing.

    watchdog_seconds = float(os.getenv("BOOT_WATCHDOG_SECONDS", "120"))

    async def boot_watchdog() -> None:
        try:
            await asyncio.sleep(watchdog_seconds)
            if not app.state.boot_completed:
                logger.critical(
                    "Boot watchdog exceeded %.0fs; forcing process exit",
                    watchdog_seconds,
                )
                os._exit(1)
        except asyncio.CancelledError:
            raise

    watchdog_task = asyncio.create_task(boot_watchdog(), name="boot-watchdog")
    container: Optional[ServiceContainer] = None
    cleanup_task: Optional[asyncio.Task] = None
    boot_succeeded = False

    try:
        settings.validate_required()
        logger.info("Building service container...")
        container = build_container()
        app.state.container = container
        redis_client = _resolve_redis(container)

        # Preserve the existing V71 installer chain and order.
        from services.v71_13_production_runtime_convergence import install_production_runtime
        install_production_runtime(app, container)
        logger.info("V71.13 production runtime convergence installed.")

        from services.v71_14_production_route_event_wiring import install_route_event_wiring
        install_route_event_wiring(app, container)
        logger.info("V71.14 production route/event wiring installed.")

        from services.v71_15_legacy_runtime_authority_fencing import install_legacy_runtime_fencing
        install_legacy_runtime_fencing(app, container)
        logger.info("V71.15 legacy runtime authority fencing installed.")

        from services.v71_17_durable_workforce_execution import install_workforce_execution_coordinator
        install_workforce_execution_coordinator(app, container)
        logger.info("V71.17 durable workforce execution coordinator installed.")

        from services.v71_17_durable_workforce_execution_convergence import install_durable_workforce_execution_convergence
        install_durable_workforce_execution_convergence(app, container)
        logger.info("V71.17 durable workforce execution convergence installed.")

        from services.v71_18_workforce_control_plane_persistence import install_workforce_control_plane_persistence
        install_workforce_control_plane_persistence(app, container)
        logger.info("V71.18 workforce control-plane persistence installed.")

        await boot_infrastructure(app, container)
        logger.info("Phased boot sequence complete.")

        if service_mode in ("full", "core", "director"):
            mounted = await _mount_director_routes_directly(app)
            app.openapi_schema = None
            if not mounted:
                # Director is a core surface in these modes; readiness must not
                # report healthy when its direct route mount failed.
                _record_boot_warning(
                    "director_routes",
                    RuntimeError("Director routes did not mount"),
                    app,
                    critical=True,
                )

        if service_mode in ("full", "api", ""):
            try:
                from routes.event_api import init_event_api
                await init_event_api()
                logger.info("Event API initialized.")
            except ImportError:
                logger.debug("Event API unavailable; event capture skipped.")
            except Exception as exc:
                _record_boot_warning("event_api_init", exc, app)

            try:
                _init_api_stores(app, redis_client)
            except Exception as exc:
                _record_boot_warning("api_stores", exc, app)

        # Director HTTP client exists only when the proxy routes were mounted.
        # Closed once, by shutdown_registry (runs in both failure and success
        # shutdown paths via graceful_shutdown).
        if app.state.director_mount_mode == "proxy":
            app.state.director_http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0, read=30.0, write=30.0, pool=10.0
                ),
                follow_redirects=False,
            )
            await shutdown_registry.register(
                "DirectorHTTPClient",
                lambda: app.state.director_http_client.aclose()
                if app.state.director_http_client is not None else None,
                timeout_seconds=10.0,
            )

        # Draft cleanup runs in every mode that created the draft store
        # (previously api-only, so "full" mode never purged expired drafts).
        if hasattr(app.state, "draft_store"):
            cleanup_task = asyncio.create_task(
                _draft_cleanup_loop(app), name="draft-cleanup-loop"
            )

        app.state.startup_duration_seconds = time.monotonic() - monotonic_start
        app.state.startup_completed_at = datetime.now(timezone.utc)
        app.state.boot_completed = True
        boot_succeeded = True

        _startup_completed_at = app.state.startup_completed_at
        _startup_duration_seconds = app.state.startup_duration_seconds

        logger.info(
            "app.boot.complete",
            extra={
                "event": "app.boot.complete",
                "boot_id": app.state.boot_id,
                "duration_s": round(app.state.startup_duration_seconds, 3),
                "warnings": len(app.state.boot_warnings),
                "route_modules": len(app.state.route_registry),
            },
        )
        for warning in app.state.boot_warnings:
            logger.warning(
                "Boot warning [%s]: %s",
                warning["service"], warning["error"],
            )

        await _cancel_task(watchdog_task, "Boot watchdog")
        yield

    except BaseException as exc:
        if not isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            _record_boot_warning("application_boot", exc, app, critical=True)
        app.state.boot_completed = False
        await _cancel_task(cleanup_task, "Draft cleanup")
        await _cancel_task(
            getattr(app.state, "audit_log_seed_task", None), "Audit-log seed"
        )
        await _best_effort_shutdown(container)
        await _cancel_task(watchdog_task, "Boot watchdog")
        raise

    finally:
        if boot_succeeded:
            logger.info(
                "app.shutdown.start",
                extra={"event": "app.shutdown.start", "boot_id": app.state.boot_id},
            )
            shutdown_start = time.monotonic()
            app.state.boot_completed = False

            await _cancel_task(cleanup_task, "Draft cleanup")
            await _cancel_task(
                getattr(app.state, "audit_log_seed_task", None), "Audit-log seed"
            )

            # Drain outstanding best-effort API counters before the container
            # is shut down. They are bounded and never block request handling.
            counter_tasks = list(getattr(app.state, "api_counter_tasks", set()))
            if counter_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*counter_tasks, return_exceptions=True),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    for task in counter_tasks:
                        task.cancel()

            try:
                summary = await graceful_shutdown(container) if container else {}
            except Exception:
                summary = {}
                logger.exception("graceful_shutdown raised")

            # DirectorHTTPClient is closed by its shutdown_registry hook above.
            app.state.director_http_client = None

            logger.info(
                "app.shutdown.complete",
                extra={
                    "event": "app.shutdown.complete",
                    "boot_id": app.state.boot_id,
                    "duration_s": round(time.monotonic() - shutdown_start, 3),
                    "hooks_total": summary.get("total", 0) if isinstance(summary, dict) else 0,
                    "hooks_executed": summary.get("executed", 0) if isinstance(summary, dict) else 0,
                    "hooks_failed": summary.get("failed", 0) if isinstance(summary, dict) else 0,
                    "hooks_timed_out": summary.get("timed_out", 0) if isinstance(summary, dict) else 0,
                    "budget_exhausted": summary.get("budget_exhausted", False)
                    if isinstance(summary, dict) else False,
                },
            )


# ===========================================================================
# APPLICATION FACTORY
# ===========================================================================

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kanyoza Systems AI Platform",
        description="Multi-tenant AI platform with industry plugins and control-plane services",
        version=PLATFORM_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    app.state.boot_completed = False
    app.state.container = None
    app.state.boot_warnings = []
    app.state.route_registry = []
    app.state.route_registration_failures = []
    app.state.director_routes_mounted = False
    app.state.director_mount_mode = None
    app.state.director_http_client = None
    app.state.boot_id = None
    # Single read of the environment; lifespan and Director mounting use this.
    app.state.service_mode = _get_service_mode()
    app.state.python_version = None
    app.state.api_counter_tasks = set()
    app.state.api_counter_dropped = 0
    app.state.startup_started_at = None
    app.state.startup_completed_at = None
    app.state.startup_duration_seconds = 0.0

    # KDO Engineering Console: explicit PostgreSQL provider only; no demo DB.
    app.state.kdo_engineering_console_provider = None
    try:
        from services.kdo_engineering_console_provider import (
            create_console_provider_from_environment,
        )
        app.state.kdo_engineering_console_provider = (
            create_console_provider_from_environment()
        )
        if app.state.kdo_engineering_console_provider is not None:
            logger.info(
                "KDO engineering console PostgreSQL provider configured; "
                "connectivity is checked per request."
            )
        else:
            logger.info(
                "KDO engineering console provider not configured; "
                "console APIs will fail closed with 503."
            )
    except Exception as exc:
        logger.error(
            "KDO engineering console provider configuration failed: %s",
            _sanitize(exc, 240),
        )

    _configure_middleware(app)
    _configure_cors(app)

    # Starlette executes the last-added middleware outermost.
    app.add_middleware(StartupGateMiddleware)
    app.add_middleware(ApiCallCounterMiddleware)

    _register_health_endpoints(app)
    _mount_routes(app)  # raises here if a required route module is not verified
    _mount_event_api(app)
    _mount_director_proxy(app)

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
            "health": {
                "live": "/health/live",
                "ready": "/health/ready",
                "detailed": "/health/detailed",
                "routes": "/health/routes",
                "version": "/health/version",
            },
        }

    return app


# ===========================================================================
# ASGI APPLICATION ASSEMBLY
# ===========================================================================

fastapi_app = create_app()
app = create_socket_app(fastapi_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
    )
