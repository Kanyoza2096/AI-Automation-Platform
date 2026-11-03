"""Phase 17.3: explicit, fail-closed production worker bootstrap.

This module is deliberately not imported by the web application. A deployment
must invoke it from a dedicated worker process with explicit PostgreSQL
configuration and fully constructed governed dependencies.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue
from services.kdo_engineering_worker_service import run_engineering_build_worker

logger = logging.getLogger("kdo.engineering.worker.bootstrap")


class EngineeringWorkerBootstrapError(RuntimeError):
    """Production worker configuration or dependency validation failed."""


@dataclass(frozen=True)
class ProductionWorkerConfig:
    enabled: bool
    database_url: str
    worker_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    lease_seconds: int = 300
    heartbeat_interval_seconds: float | None = None
    poll_interval_seconds: float = 2.0
    max_poll_interval_seconds: float = 30.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProductionWorkerConfig":
        env = os.environ if environ is None else environ
        enabled = env.get("KDO_ENGINEERING_WORKER_ENABLED", "").strip().lower() == "true"
        return cls(
            enabled=enabled,
            database_url=env.get("KDO_ENGINEERING_DATABASE_URL", "").strip(),
            worker_id=env.get("KDO_ENGINEERING_WORKER_ID", "").strip(),
            tenant_id=env.get("KDO_ENGINEERING_TENANT_ID", "").strip(),
            workspace_id=env.get("KDO_ENGINEERING_WORKSPACE_ID", "").strip(),
            actor_id=env.get("KDO_ENGINEERING_WORKER_ACTOR_ID", "").strip(),
            lease_seconds=_positive_int(env.get("KDO_ENGINEERING_LEASE_SECONDS", "300"), "lease_seconds"),
            heartbeat_interval_seconds=_optional_float(env.get("KDO_ENGINEERING_HEARTBEAT_SECONDS")),
            poll_interval_seconds=_positive_float(env.get("KDO_ENGINEERING_POLL_SECONDS", "2"), "poll_seconds"),
            max_poll_interval_seconds=_positive_float(env.get("KDO_ENGINEERING_MAX_POLL_SECONDS", "30"), "max_poll_seconds"),
        )


def _positive_int(raw: str, field: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise EngineeringWorkerBootstrapError(f"invalid_{field}") from exc
    if value < 1:
        raise EngineeringWorkerBootstrapError(f"invalid_{field}")
    return value


def _positive_float(raw: str, field: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise EngineeringWorkerBootstrapError(f"invalid_{field}") from exc
    if not 0 < value < float("inf"):
        raise EngineeringWorkerBootstrapError(f"invalid_{field}")
    return value


def _optional_float(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return _positive_float(raw, "heartbeat_seconds")


def validate_production_worker_config(config: ProductionWorkerConfig) -> None:
    if not isinstance(config, ProductionWorkerConfig):
        raise EngineeringWorkerBootstrapError("production_worker_config_required")
    if config.enabled is not True:
        raise EngineeringWorkerBootstrapError("production_worker_not_explicitly_enabled")
    parsed = urlparse(config.database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise EngineeringWorkerBootstrapError("postgres_database_url_required")
    if not all(isinstance(value, str) and value.strip() for value in (
        config.worker_id, config.tenant_id, config.workspace_id, config.actor_id
    )):
        raise EngineeringWorkerBootstrapError("worker_identity_and_scope_required")
    if isinstance(config.lease_seconds, bool) or not 30 <= config.lease_seconds <= 3600:
        raise EngineeringWorkerBootstrapError("lease_seconds_out_of_range")
    if config.heartbeat_interval_seconds is not None and not (
        0 < config.heartbeat_interval_seconds < config.lease_seconds
    ):
        raise EngineeringWorkerBootstrapError("heartbeat_interval_out_of_range")
    if config.max_poll_interval_seconds < config.poll_interval_seconds:
        raise EngineeringWorkerBootstrapError("poll_backoff_range_invalid")


@dataclass
class WorkerHealth:
    status: str = "starting"
    worker_id: str = ""
    started_at: str | None = None
    last_job_result_at: str | None = None
    last_job_id: str | None = None
    last_error_code: str | None = None
    stopped_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "worker_id": self.worker_id,
            "started_at": self.started_at,
            "last_job_result_at": self.last_job_result_at,
            "last_job_id": self.last_job_id,
            "last_error_code": self.last_error_code,
            "stopped_at": self.stopped_at,
        }


def _probe_postgres(connection_factory: Callable[[], Any]) -> None:
    connection = cursor = None
    try:
        connection = connection_factory()
        cursor = connection.cursor()
        cursor.execute("SELECT to_regclass('public.kdo_engineering_build_jobs'), "
                       "to_regclass('public.kdo_engineering_tasks'), "
                       "to_regclass('public.kdo_engineering_workspaces')")
        row = cursor.fetchone()
        if not row or any(value is None for value in row):
            raise EngineeringWorkerBootstrapError("engineering_postgres_schema_not_ready")
    except EngineeringWorkerBootstrapError:
        raise
    except Exception as exc:
        raise EngineeringWorkerBootstrapError("postgres_connectivity_or_schema_probe_failed") from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def run_production_engineering_worker(
    *,
    config: ProductionWorkerConfig,
    connection_factory: Callable[[], Any],
    queue: Any,
    workflow: Any,
    executor: Callable[..., Mapping[str, Any]],
    stopping: threading.Event,
    health_sink: Callable[[Mapping[str, Any]], None] | None = None,
    max_jobs: int | None = None,
) -> dict[str, Any]:
    """Validate production dependencies, then run the scoped worker in-process.

    The caller owns process supervision and signal handling. No module import,
    web startup, or default application path starts this worker.
    """
    validate_production_worker_config(config)
    if not isinstance(queue, PostgresEngineeringBuildQueue):
        raise EngineeringWorkerBootstrapError("postgres_queue_required_no_sqlite_fallback")
    if not callable(connection_factory) or not callable(executor):
        raise EngineeringWorkerBootstrapError("connection_factory_and_executor_required")
    if workflow is None or not isinstance(stopping, threading.Event):
        raise EngineeringWorkerBootstrapError("governed_workflow_and_stop_event_required")
    _probe_postgres(connection_factory)

    health = WorkerHealth(
        status="running",
        worker_id=config.worker_id,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    def publish() -> None:
        if health_sink is None:
            return
        try:
            health_sink(health.snapshot())
        except Exception:
            logger.exception("Engineering worker health callback failed")

    def on_result(job: Any, result: Mapping[str, Any]) -> None:
        health.last_job_id = str(getattr(job, "job_id", "")) or None
        health.last_job_result_at = datetime.now(timezone.utc).isoformat()
        health.last_error_code = None if result.get("status") in {"awaiting_review", "completed"} else str(result.get("status", "execution_result"))
        publish()

    publish()
    try:
        result = run_engineering_build_worker(
            queue=queue,
            workflow=workflow,
            worker_id=config.worker_id,
            tenant_id=config.tenant_id,
            workspace_id=config.workspace_id,
            actor_id=config.actor_id,
            executor=executor,
            stopping=stopping,
            lease_seconds=config.lease_seconds,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
            max_poll_interval_seconds=config.max_poll_interval_seconds,
            max_jobs=max_jobs,
            on_result=on_result,
        )
        health.status = "stopped"
        health.stopped_at = datetime.now(timezone.utc).isoformat()
        publish()
        return {**result, "health": health.snapshot()}
    except Exception as exc:
        health.status = "failed"
        health.last_error_code = type(exc).__name__
        health.stopped_at = datetime.now(timezone.utc).isoformat()
        publish()
        raise
