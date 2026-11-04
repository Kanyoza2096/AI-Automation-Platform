"""Local/dedicated-process entrypoint for the governed engineering worker.

The composition factory is deliberately injected by the host. This entrypoint
never invents an executor, falls back to SQLite, or starts from web startup.
"""
from __future__ import annotations

import importlib
import logging
import os
import signal
import threading
from collections.abc import Mapping
from typing import Any, Callable

import psycopg

from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue
from services.kdo_engineering_worker_bootstrap import (
    ProductionWorkerConfig,
    run_production_engineering_worker,
)

logger = logging.getLogger("kdo.engineering.worker.entrypoint")


def _load_factory(spec: str) -> Callable[..., Mapping[str, Any]]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise RuntimeError("KDO_ENGINEERING_WORKER_FACTORY must use module:callable format")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("engineering_worker_composition_factory_not_callable")
    return factory


def _max_jobs_from_env() -> int | None:
    raw = os.environ.get("KDO_ENGINEERING_WORKER_MAX_JOBS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("invalid_KDO_ENGINEERING_WORKER_MAX_JOBS") from exc
    if value < 1:
        raise RuntimeError("invalid_KDO_ENGINEERING_WORKER_MAX_JOBS")
    return value


def _health_sink(snapshot: Mapping[str, Any]) -> None:
    # Deliberately log only bounded, non-secret lifecycle metadata.
    logger.info("engineering_worker_health status=%s worker_id=%s last_job_id=%s last_error_code=%s",
                snapshot.get("status"), snapshot.get("worker_id"),
                snapshot.get("last_job_id"), snapshot.get("last_error_code"))


def main() -> int:
    config = ProductionWorkerConfig.from_env()
    if not config.enabled:
        logger.info("engineering worker disabled; no queue polling started")
        return 0

    factory_spec = os.environ.get("KDO_ENGINEERING_WORKER_FACTORY", "").strip()
    if not factory_spec:
        raise RuntimeError("KDO_ENGINEERING_WORKER_FACTORY_required_when_worker_enabled")
    factory = _load_factory(factory_spec)
    connection_factory = lambda: psycopg.connect(config.database_url, connect_timeout=5)
    dependencies = factory(config=config, connection_factory=connection_factory)
    if not isinstance(dependencies, Mapping):
        raise RuntimeError("engineering_worker_factory_must_return_mapping")

    queue = dependencies.get("queue")
    if not isinstance(queue, PostgresEngineeringBuildQueue):
        raise RuntimeError("composition_factory_must_return_postgres_engineering_queue")
    workflow = dependencies.get("workflow")
    executor = dependencies.get("executor")
    if workflow is None or not callable(executor):
        raise RuntimeError("composition_factory_must_supply_governed_workflow_and_executor")
    health_sink = dependencies.get("health_sink", _health_sink)
    cleanup = dependencies.get("cleanup")
    if cleanup is not None and not callable(cleanup):
        raise RuntimeError("engineering_worker_cleanup_must_be_callable")

    stopping = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def request_stop(signum: int, _frame: Any) -> None:
        logger.info("engineering worker shutdown requested signal=%s", signum)
        stopping.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)

    try:
        result = run_production_engineering_worker(
            config=config,
            connection_factory=connection_factory,
            queue=queue,
            workflow=workflow,
            executor=executor,
            stopping=stopping,
            health_sink=health_sink,
            max_jobs=_max_jobs_from_env(),
        )
        logger.info("engineering worker stopped claimed=%s completed=%s execution_errors=%s claim_errors=%s",
                    result.get("claimed"), result.get("completed"),
                    result.get("execution_errors"), result.get("claim_errors"))
        return 0
    finally:
        stopping.set()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if callable(cleanup):
            cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Error type only: connection strings, credentials, and exception details
        # are intentionally not printed by this process boundary.
        logger.error("engineering worker startup/execution failed error_type=%s", type(exc).__name__)
        raise SystemExit(1)
