import pytest

from services.action_kernel import ActionContract
from services.kdo_engineering_worker_authorization import (
    ACTION_TYPE,
    CouncilActionKernelWorkerAuthorizer,
    GovernedWorkerAuthorizationError,
)


class FakeDatabase:
    def table(self, _name):
        raise AssertionError("constructor must not perform a receipt read")


class FakeKernel:
    def __init__(self, db=None, allow_in_memory=False):
        self.db = db
        self.allow_in_memory = allow_in_memory
        self.contracts = {}

    def execute(self, **_kwargs):
        raise AssertionError("execution is not part of constructor validation")

    def get_contract_metadata(self, action_type):
        contract = self.contracts.get(action_type)
        if contract is None:
            return None
        return {
            "action_type": contract.action_type,
            "risk_level": contract.risk_level,
            "required_parameters": list(contract.required_parameters),
            "requires_approval": contract.requires_approval,
            "inline_with_decision": contract.inline_with_decision,
        }

    def register(self, contract):
        if contract.action_type in self.contracts:
            raise ValueError("duplicate contract")
        self.contracts[contract.action_type] = contract


def test_authorizer_requires_database_backed_action_kernel():
    with pytest.raises(GovernedWorkerAuthorizationError, match="durable_action_kernel_database_required"):
        CouncilActionKernelWorkerAuthorizer(FakeKernel())


def test_authorizer_rejects_in_memory_kernel_even_with_database():
    with pytest.raises(GovernedWorkerAuthorizationError, match="in_memory_action_kernel_forbidden"):
        CouncilActionKernelWorkerAuthorizer(FakeKernel(FakeDatabase(), allow_in_memory=True))


def test_authorizer_registers_high_risk_approval_required_contract():
    kernel = FakeKernel(FakeDatabase())
    authorizer = CouncilActionKernelWorkerAuthorizer(kernel)

    assert isinstance(authorizer, CouncilActionKernelWorkerAuthorizer)
    contract = kernel.contracts[ACTION_TYPE]
    assert isinstance(contract, ActionContract)
    assert contract.risk_level == "high"
    assert contract.requires_approval is True
    assert contract.inline_with_decision is True
    assert set(contract.required_parameters) == {
        "tenant_id", "workspace_id", "task_id", "requested_action",
        "payload_digest", "authorization_request_id",
    }


def test_authorizer_rejects_pre_registered_weak_contract():
    kernel = FakeKernel(FakeDatabase())
    kernel.register(ActionContract(
        action_type=ACTION_TYPE,
        risk_level="low",
        required_parameters=("task_id",),
        requires_approval=False,
        inline_with_decision=True,
        handler=lambda *_: {"status": "succeeded"},
    ))
    with pytest.raises(GovernedWorkerAuthorizationError, match="worker_authorization_contract_mismatch"):
        CouncilActionKernelWorkerAuthorizer(kernel)
