import logging

import pytest

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from scripts import run_kdo_engineering_worker as entrypoint


def test_entrypoint_is_disabled_by_default_and_does_not_load_factory(monkeypatch, caplog):
    monkeypatch.setattr(
        entrypoint.ProductionWorkerConfig,
        "from_env",
        classmethod(lambda cls: ProductionWorkerConfig(
            enabled=False, database_url="", worker_id="", tenant_id="",
            workspace_id="", actor_id="",
        )),
    )
    monkeypatch.delenv("KDO_ENGINEERING_WORKER_FACTORY", raising=False)
    with caplog.at_level(logging.INFO):
        assert entrypoint.main() == 0
    assert "engineering worker disabled" in caplog.text


def test_enabled_entrypoint_requires_explicit_composition_factory(monkeypatch):
    monkeypatch.setattr(
        entrypoint.ProductionWorkerConfig,
        "from_env",
        classmethod(lambda cls: ProductionWorkerConfig(
            enabled=True, database_url="postgresql://worker:secret@db.example.test:5432/kdo",
            worker_id="worker-a", tenant_id="tenant-a", workspace_id="workspace-a",
            actor_id="engineering-worker",
        )),
    )
    monkeypatch.delenv("KDO_ENGINEERING_WORKER_FACTORY", raising=False)
    with pytest.raises(RuntimeError, match="FACTORY_required"):
        entrypoint.main()


@pytest.mark.parametrize("spec", ["", "missing-separator", ":factory", "module:"])
def test_factory_spec_must_be_module_and_callable(spec):
    with pytest.raises(RuntimeError, match="module:callable"):
        entrypoint._load_factory(spec)


def test_max_jobs_must_be_positive_integer(monkeypatch):
    monkeypatch.setenv("KDO_ENGINEERING_WORKER_MAX_JOBS", "0")
    with pytest.raises(RuntimeError, match="invalid_KDO_ENGINEERING_WORKER_MAX_JOBS"):
        entrypoint._max_jobs_from_env()
