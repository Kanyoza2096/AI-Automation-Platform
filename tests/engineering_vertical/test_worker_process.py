from types import SimpleNamespace

import pytest

from services.kdo_engineering_worker_process import (
    run_worker,
    validate_docker_runtime,
)


def test_worker_process_is_inert_by_default():
    assert run_worker({}) == 0


def test_enabled_worker_requires_trusted_host_factory():
    env = {
        "KDO_ENGINEERING_WORKER_ENABLED": "true",
        "KDO_ENGINEERING_DATABASE_URL": "postgresql://worker:secret@localhost:5432/kdo",
        "KDO_ENGINEERING_WORKER_ID": "worker-a",
        "KDO_ENGINEERING_TENANT_ID": "tenant-a",
        "KDO_ENGINEERING_WORKSPACE_ID": "workspace-a",
        "KDO_ENGINEERING_WORKER_ACTOR_ID": "engineering-worker",
    }
    with pytest.raises(RuntimeError, match="trusted_worker_host_factory_required"):
        run_worker(env)


def test_enabled_worker_rejects_malformed_factory_path():
    env = {
        "KDO_ENGINEERING_WORKER_ENABLED": "true",
        "KDO_ENGINEERING_DATABASE_URL": "postgresql://worker:secret@localhost:5432/kdo",
        "KDO_ENGINEERING_WORKER_ID": "worker-a",
        "KDO_ENGINEERING_TENANT_ID": "tenant-a",
        "KDO_ENGINEERING_WORKSPACE_ID": "workspace-a",
        "KDO_ENGINEERING_WORKER_ACTOR_ID": "engineering-worker",
        "KDO_ENGINEERING_WORKER_HOST_FACTORY": "not-a-module-path",
    }
    with pytest.raises(RuntimeError, match="trusted_worker_host_factory_path_invalid"):
        # The factory path is validated before the Docker runtime is probed.
        run_worker(env)


def test_docker_runtime_fails_closed_when_cli_is_missing():
    with pytest.raises(RuntimeError, match="docker_cli_unavailable"):
        validate_docker_runtime(which=lambda _binary: None)


def test_docker_runtime_fails_closed_when_daemon_is_unreachable():
    with pytest.raises(RuntimeError, match="docker_daemon_unavailable"):
        validate_docker_runtime(
            which=lambda binary: f"/usr/bin/{binary}",
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
        )


def test_docker_runtime_accepts_reachable_daemon():
    validate_docker_runtime(
        which=lambda binary: f"/usr/bin/{binary}",
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="27.0.1\n", stderr=""),
    )
