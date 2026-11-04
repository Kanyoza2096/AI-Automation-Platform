from pathlib import Path

import pytest

from services.kdo_engineering_phase16_4 import PostgresEngineeringBuildQueue
from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from services.kdo_engineering_worker_composition import (
    EngineeringWorkerCompositionError,
    build_worker_dependencies,
    build_gateway_worker_dependencies,
)


def config(**overrides):
    values = dict(
        enabled=True,
        database_url="postgresql://worker:secret@localhost:5432/kdo_test",
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
    )
    values.update(overrides)
    return ProductionWorkerConfig(**values)


def test_composition_wires_postgres_workflow_and_containerized_quality_executor(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()
    seen = {}

    def connection_factory():
        raise AssertionError("composition must not connect before worker bootstrap")

    def build_executor(*args, **kwargs):
        return {"status": "built"}

    def registry_factory(*args):
        return object()

    def repair_capabilities_factory(*args):
        return {"python"}

    def authorize_workflow(*args):
        return True

    def authorize_pipeline(*args):
        return True

    deps = build_worker_dependencies(
        config=config(),
        connection_factory=connection_factory,
        source_repository_path=source,
        managed_workspace_root=managed,
        pinned_worker_image="registry.example/kdo-worker@sha256:" + "a" * 64,
        workflow_authorize=authorize_workflow,
        build_executor=build_executor,
        registry_factory=registry_factory,
        repair_capabilities_factory=repair_capabilities_factory,
        pipeline_authorize=authorize_pipeline,
        admission_receipt_verifier=lambda scope, receipt: True,
    )

    assert isinstance(deps["queue"], PostgresEngineeringBuildQueue)
    assert deps["workflow"].tasks.__class__.__name__ == "PostgresEngineeringTaskRepository"
    assert deps["workflow"].workspaces.__class__.__name__ == "PostgresEngineeringWorkspaceRepository"
    assert callable(deps["executor"])
    assert callable(deps["cleanup"])


def test_composition_rejects_missing_host_owned_authority_callbacks(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    with pytest.raises(EngineeringWorkerCompositionError, match="host_owned_dependencies_required"):
        build_worker_dependencies(
            config=config(),
            connection_factory=lambda: None,
            source_repository_path=source,
            managed_workspace_root=managed,
            pinned_worker_image="registry.example/kdo-worker@sha256:" + "a" * 64,
            workflow_authorize=lambda *args: True,
            build_executor=lambda *args, **kwargs: {},
            registry_factory=lambda *args: object(),
            repair_capabilities_factory=lambda *args: {"python"},
            pipeline_authorize=None,
            admission_receipt_verifier=lambda scope, receipt: True,
        )


def test_composition_rejects_non_digest_pinned_image(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    with pytest.raises(EngineeringWorkerCompositionError, match="digest_pinned"):
        build_worker_dependencies(
            config=config(),
            connection_factory=lambda: None,
            source_repository_path=source,
            managed_workspace_root=managed,
            pinned_worker_image="python:3.12-slim",
            workflow_authorize=lambda *args: True,
            build_executor=lambda *args, **kwargs: {},
            registry_factory=lambda *args: object(),
            repair_capabilities_factory=lambda *args: {"python"},
            pipeline_authorize=lambda *args: True,
            admission_receipt_verifier=lambda scope, receipt: True,
        )


def test_composition_rejects_worker_not_explicitly_enabled(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    with pytest.raises(Exception, match="not_explicitly_enabled"):
        build_worker_dependencies(
            config=config(enabled=False),
            connection_factory=lambda: None,
            source_repository_path=source,
            managed_workspace_root=managed,
            pinned_worker_image="registry.example/kdo-worker@sha256:" + "a" * 64,
            workflow_authorize=lambda *args: True,
            build_executor=lambda *args, **kwargs: {},
            registry_factory=lambda *args: object(),
            repair_capabilities_factory=lambda *args: {"python"},
            pipeline_authorize=lambda *args: True,
            admission_receipt_verifier=lambda scope, receipt: True,
        )



def test_gateway_composition_requires_and_wires_task_aware_repair_capabilities(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    class Gateway:
        def complete_structured(self, *_args, **_kwargs):
            raise AssertionError("gateway must not be called during composition")

    deps = build_gateway_worker_dependencies(
        config=config(),
        connection_factory=lambda: None,
        source_repository_path=source,
        managed_workspace_root=managed,
        pinned_worker_image="registry.example/kdo-worker@sha256:" + "b" * 64,
        gateway=Gateway(),
        workflow_authorize=lambda *_: True,
        pipeline_authorize=lambda *_: True,
        admission_receipt_verifier=lambda scope, receipt: True,
        repair_capabilities_factory=lambda task, steps: {"backend", "api"},
    )
    assert isinstance(deps["queue"], PostgresEngineeringBuildQueue)
    assert callable(deps["executor"])


def test_gateway_composition_rejects_missing_task_aware_repair_capabilities(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    class Gateway:
        def complete_structured(self, *_args, **_kwargs):
            raise AssertionError("gateway must not be called during composition")

    with pytest.raises(EngineeringWorkerCompositionError, match="task_aware_repair_capability_factory_required"):
        build_gateway_worker_dependencies(
            config=config(),
            connection_factory=lambda: None,
            source_repository_path=source,
            managed_workspace_root=managed,
            pinned_worker_image="registry.example/kdo-worker@sha256:" + "b" * 64,
            gateway=Gateway(),
            workflow_authorize=lambda *_: True,
            pipeline_authorize=lambda *_: True,
        admission_receipt_verifier=lambda scope, receipt: True,
            repair_capabilities_factory=None,
        )
