"""Phase 5: HTTP adapters delegate orchestration to application use-cases."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from application.action_intents import (
    ActionIntentApplicationService,
    ApproveActionCommand,
    EnqueueActionCommand,
    OperatorProvenance,
)

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_action_route_delegates_orchestration_to_application_service() -> None:
    source = (ROOT / "api" / "action_intent_routes.py").read_text(encoding="utf-8")
    assert "action_intent_application" in source
    assert "services.action_kernel" not in source
    assert "await kernel.enqueue(" not in source
    assert "await kernel.approve(" not in source


def test_application_layer_does_not_import_fastapi_or_infrastructure() -> None:
    modules = _imports(ROOT / "application" / "action_intents.py")
    assert not any(m == "fastapi" or m.startswith("services") for m in modules)


class FakeKernel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def enqueue(self, **kwargs):
        self.calls.append(("enqueue", kwargs))
        return {"ok": True, "status": "pending", "intent_id": "i-1"}

    async def approve(self, **kwargs):
        self.calls.append(("approve", kwargs))
        return {"ok": True, "status": "pending", "intent_id": kwargs["intent_id"]}


@pytest.mark.asyncio
async def test_enqueue_use_case_preserves_authenticated_workspace_and_provenance():
    kernel = FakeKernel()
    service = ActionIntentApplicationService(kernel)
    result = await service.enqueue(
        EnqueueActionCommand(
            workspace_id="ws-a",
            action_type="notify_operator",
            parameters={"message": "hello"},
            risk_level="low",
            decision_id=None,
            correlation_id="c-1",
            idempotency_key="k-1",
            source="test",
            operator=OperatorProvenance("user-1", "operator", "ws-a"),
        )
    )
    assert result["operator"]["user_id"] == "user-1"
    assert kernel.calls[0][1]["workspace_id"] == "ws-a"
    assert kernel.calls[0][1]["source"] == "kcpg:test"


@pytest.mark.asyncio
async def test_enqueue_use_case_rejects_workspace_provenance_mismatch():
    service = ActionIntentApplicationService(FakeKernel())
    with pytest.raises(PermissionError):
        await service.enqueue(
            EnqueueActionCommand(
                workspace_id="ws-b", action_type="notify_operator", parameters={},
                risk_level="low", decision_id=None, correlation_id=None,
                idempotency_key="k-2", source="test",
                operator=OperatorProvenance("user-1", "operator", "ws-a"),
            )
        )


@pytest.mark.asyncio
async def test_approval_use_case_enforces_operator_role_before_port_call():
    kernel = FakeKernel()
    service = ActionIntentApplicationService(kernel)
    with pytest.raises(PermissionError):
        await service.approve(
            ApproveActionCommand(
                workspace_id="ws-a", intent_id="i-1",
                approver=OperatorProvenance("user-1", "viewer", "ws-a"),
            )
        )
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_approval_use_case_delegates_only_after_application_authorization():
    kernel = FakeKernel()
    service = ActionIntentApplicationService(kernel)
    result = await service.approve(
        ApproveActionCommand(
            workspace_id="ws-a", intent_id="i-1",
            approver=OperatorProvenance("user-1", "owner", "ws-a"),
        )
    )
    assert result["authority_source"] == "kcpg"
    assert kernel.calls[0][1]["approver_role"] == "owner"
