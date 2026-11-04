from contextlib import contextmanager

import pytest

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig
from services.kdo_engineering_trusted_worker_host import (
    TrustedEngineeringWorkerHostError,
    build_trusted_gateway_worker_host,
)


class FakeDatabase:
    def table(self, _name):
        raise AssertionError("receipt reads must not happen during composition")


class FakeActionKernel:
    def __init__(self, *, allow_in_memory=False):
        self.db = FakeDatabase()
        self.allow_in_memory = allow_in_memory
        self.contracts = {}

    def execute(self, *_args, **_kwargs):
        raise AssertionError("no action executes during composition")

    def get_contract_metadata(self, action_type):
        return self.contracts.get(action_type)

    def register(self, contract):
        self.contracts[contract.action_type] = {
            "action_type": contract.action_type,
            "risk_level": contract.risk_level,
            "required_parameters": contract.required_parameters,
            "requires_approval": contract.requires_approval,
            "inline_with_decision": contract.inline_with_decision,
        }


class FakeGateway:
    def complete_structured(self, *_args, **_kwargs):
        raise AssertionError("gateway must not be called during composition")


def config():
    return ProductionWorkerConfig(
        enabled=True,
        database_url="postgresql://worker:secret@localhost:5432/kdo_test",
        worker_id="worker-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="engineering-worker",
    )


def compose(tmp_path, *, kernel=None):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()
    kernel = kernel or FakeActionKernel()
    host = build_trusted_gateway_worker_host(
        config=config(),
        connection_factory=lambda: None,
        source_repository_path=source,
        managed_workspace_root=managed,
        pinned_worker_image="registry.example/kdo-worker@sha256:" + "a" * 64,
        gateway=FakeGateway(),
        action_kernel=kernel,
        authenticated_workflow_authorizer=lambda *_args: True,
        repair_capabilities_factory=lambda _task, _steps: {"python"},
    )
    return host, kernel


def test_trusted_host_derives_council_authorizer_and_receipt_verifier(tmp_path):
    host, kernel = compose(tmp_path)

    assert callable(host["run"])
    assert host["worker_authorizer"].__class__.__name__ == "CouncilActionKernelWorkerAuthorizer"
    assert host["executor"].worker_authorizer is host["worker_authorizer"]
    assert host["admission_receipt_verifier"].__class__.__name__ == "DurableActionReceiptVerifier"
    assert "kdo_engineering_worker_action_authorize" in kernel.contracts


def test_trusted_host_rejects_in_memory_action_kernel(tmp_path):
    with pytest.raises(TrustedEngineeringWorkerHostError, match="in_memory_action_kernel_forbidden"):
        compose(tmp_path, kernel=FakeActionKernel(allow_in_memory=True))


def test_trusted_host_requires_authenticated_workflow_authorizer(tmp_path):
    source = tmp_path / "source"
    managed = tmp_path / "managed"
    source.mkdir()
    managed.mkdir()

    with pytest.raises(TrustedEngineeringWorkerHostError, match="authenticated_workflow_authorizer_required"):
        build_trusted_gateway_worker_host(
            config=config(),
            connection_factory=lambda: None,
            source_repository_path=source,
            managed_workspace_root=managed,
            pinned_worker_image="registry.example/kdo-worker@sha256:" + "a" * 64,
            gateway=FakeGateway(),
            action_kernel=FakeActionKernel(),
            authenticated_workflow_authorizer=None,
            repair_capabilities_factory=lambda _task, _steps: {"python"},
        )
