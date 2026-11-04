import pytest

from services.kdo_engineering_worker_process import run_worker


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
        run_worker(env)
