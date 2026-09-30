from pathlib import Path
import ast

from application.business_onboarding import BusinessOnboardingApplicationService, OnboardingContext

ROOT = Path(__file__).resolve().parents[2]
ROUTE = ROOT / "routes" / "business_onboarding_routes.py"


class FakePort:
    def __init__(self):
        self.calls = []

    async def understand(self, description):
        self.calls.append(("understand", description))
        return type("Profile", (), {"__dict__": {"business_type": "generic"}})()

    async def plan(self, workspace_id, description):
        self.calls.append(("plan", workspace_id, description))
        return {"plan_id": "p1", "workspace_id": workspace_id}

    def provision(self, workspace_id, plan, *, activate=False):
        self.calls.append(("provision", workspace_id, plan, activate))
        return {"workspace_id": workspace_id, "plan_id": plan["plan_id"]}


def test_application_service_owns_workspace_context_and_delegates_to_port():
    port = FakePort()
    app = BusinessOnboardingApplicationService(port)
    result = __import__("asyncio").run(app.plan(OnboardingContext("ws-1"), "coffee shop"))
    assert result["workspace_id"] == "ws-1"
    assert port.calls == [("plan", "ws-1", "coffee shop")]


def test_application_service_rejects_empty_workspace_before_port_call():
    port = FakePort()
    app = BusinessOnboardingApplicationService(port)
    try:
        app.provision(OnboardingContext("  "), {"plan_id": "p1"})
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("empty workspace must be rejected")
    assert port.calls == []


def test_route_is_transport_adapter_not_onboarding_kernel_owner():
    tree = ast.parse(ROUTE.read_text())
    source = ROUTE.read_text()
    assert "BusinessOnboardingKernel" not in source
    assert "get_service_client_from_settings" not in source
    assert "BusinessOnboardingApplicationService" in source
    assert "BusinessOnboardingServiceAdapter" in source
    # The route may compose a fallback adapter for legacy bootstrap compatibility,
    # but it must not contain the domain provisioning implementation itself.
    forbidden = {"build_business_plan", "load_playbook", "_fallback_profile"}
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not forbidden & names
