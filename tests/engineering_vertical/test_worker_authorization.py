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
        "payload_digest", "authorization_request_id", "job_id",
        "plan_digest", "lease_token_digest",
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



def _task():
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "task_id": "task-a",
        "acceptance_criteria": ["tests pass"],
    }


def _context():
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "task_id": "task-a",
        "job_id": "job-a",
        "plan_digest": "a" * 64,
        "lease_token": "lease-secret",
    }


def test_authorizer_fails_closed_without_claimed_job_context():
    kernel = FakeKernel(FakeDatabase())
    authorizer = CouncilActionKernelWorkerAuthorizer(kernel)

    assert authorizer.authorize("run_tests", _task()) is False
    assert kernel.contracts[ACTION_TYPE]


def test_authorizer_rejects_expired_or_lost_lease_before_governance():
    kernel = FakeKernel(FakeDatabase())
    authorizer = CouncilActionKernelWorkerAuthorizer(kernel)

    with authorizer.bind_execution_context(_context(), lambda: False):
        assert authorizer.authorize("run_tests", _task()) is False

    # No Action Kernel execution occurred when the lease validator denied access.
    assert kernel.contracts[ACTION_TYPE]


def test_authorizer_rejects_task_scope_mismatch_before_governance():
    kernel = FakeKernel(FakeDatabase())
    authorizer = CouncilActionKernelWorkerAuthorizer(kernel)
    wrong_task = {**_task(), "task_id": "different-task"}

    with authorizer.bind_execution_context(_context(), lambda: True):
        assert authorizer.authorize("apply_patch", wrong_task) is False


def test_authorizer_requires_context_with_job_plan_and_lease_fields():
    kernel = FakeKernel(FakeDatabase())
    authorizer = CouncilActionKernelWorkerAuthorizer(kernel)
    incomplete = {key: value for key, value in _context().items() if key != "plan_digest"}

    with pytest.raises(GovernedWorkerAuthorizationError, match="complete_worker_execution_context_required"):
        with authorizer.bind_execution_context(incomplete, lambda: True):
            pass
