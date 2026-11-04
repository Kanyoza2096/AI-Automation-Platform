"""Honcho process entrypoint for the isolated KDO engineering worker.

The web process never imports this module. The worker is inert by default.
Enabling it requires a trusted host factory and a reachable Docker daemon.
"""
from __future__ import annotations

import importlib
import logging
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Mapping
from typing import Any, Callable

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from services.kdo_engineering_trusted_worker_host import build_trusted_gateway_worker_host

logger = logging.getLogger("kdo.engineering.worker.process")


def validate_docker_runtime(
    docker_binary: str = "docker",
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Fail closed unless both the Docker CLI and its daemon are reachable."""
    if not isinstance(docker_binary, str) or not docker_binary.strip():
        raise RuntimeError("docker_binary_configuration_invalid")
    if which(docker_binary) is None:
        raise RuntimeError("docker_cli_unavailable")
    try:
        result = runner(
            [docker_binary, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except Exception as exc:
        raise RuntimeError("docker_daemon_unavailable") from exc
    if getattr(result, "returncode", 1) != 0 or not str(getattr(result, "stdout", "")).strip():
        raise RuntimeError("docker_daemon_unavailable")


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

    # Resolve trusted deployment code before probing infrastructure. Invalid
    # factory paths fail deterministically without opening DB or Docker handles.
    host_factory = _load_factory(factory_spec)

    # Verify the execution runtime before constructing DB clients or claiming jobs.
    validate_docker_runtime(env.get("KDO_ENGINEERING_DOCKER_BINARY", "docker"))

    import psycopg

    def connection_factory():
        return psycopg.connect(config.database_url, connect_timeout=5)

    # Trusted deployment code only; never task/model supplied input.
    host_arguments = host_factory(config=config, connection_factory=connection_factory)
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
    config = ProductionWorkerConfig.from_env()
    if not config.enabled:
        # Keep the Honcho-managed worker slot alive but completely inert. This
        # prevents a clean early exit from being interpreted as a failed
        # Procfile process while guaranteeing no DB poll or Docker launch.
        stopping = threading.Event()

        def stop(_signum: int, _frame: Any) -> None:
            stopping.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        logger.info("Engineering worker is disabled; waiting without polling.")
        stopping.wait()
        return
    raise SystemExit(run_worker())


if __name__ == "__main__":
    main()
