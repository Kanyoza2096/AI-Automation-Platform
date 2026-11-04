"""Honcho process entrypoint for the isolated KDO engineering worker.

The web process never imports this module. The worker is inert by default.
Enabling it requires an explicitly configured, trusted host factory that
constructs the real staging dependencies and canonical Action Kernel.
"""
from __future__ import annotations

import importlib
import logging
import os
import signal
import threading
from collections.abc import Mapping
from typing import Any, Callable

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from services.kdo_engineering_trusted_worker_host import build_trusted_gateway_worker_host

logger = logging.getLogger("kdo.engineering.worker.process")


def _load_factory(spec: str) -> Callable[..., Mapping[str, Any]]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise RuntimeError("trusted_worker_host_factory_path_invalid")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("trusted_worker_host_factory_not_callable")
    return factory


def run_worker(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    config = ProductionWorkerConfig.from_env(env)
    if not config.enabled:
        logger.info("Engineering worker is disabled; process exits without polling.")
        return 0

    factory_spec = env.get("KDO_ENGINEERING_WORKER_HOST_FACTORY", "").strip()
    if not factory_spec:
        raise RuntimeError("trusted_worker_host_factory_required_when_worker_enabled")

    import psycopg

    def connection_factory():
        return psycopg.connect(config.database_url, connect_timeout=5)

    # Trusted deployment code only; never task/model supplied input.
    host_arguments = _load_factory(factory_spec)(
        config=config, connection_factory=connection_factory
    )
    if not isinstance(host_arguments, Mapping):
        raise RuntimeError("trusted_worker_host_factory_must_return_mapping")

    host = build_trusted_gateway_worker_host(
        config=config,
        connection_factory=connection_factory,
        **dict(host_arguments),
    )
    stopping = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def health_sink(snapshot: Mapping[str, Any]) -> None:
        logger.info("Engineering worker health: %s", dict(snapshot))

    result = host["run"](stopping=stopping, health_sink=health_sink)
    logger.info("Engineering worker stopped: status=%s", result.get("status", "unknown"))
    return 0


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(run_worker())


if __name__ == "__main__":
    main()
