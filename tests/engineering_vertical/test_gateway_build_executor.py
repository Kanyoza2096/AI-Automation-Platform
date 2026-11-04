import subprocess
from types import SimpleNamespace

import pytest

from services.kdo_engineering_gateway_agents import (
    GatewayAgentError,
    make_gateway_build_executor,
)


class FakeGateway:
    def __init__(self, patch):
        self.patch = patch
        self.calls = []

    def complete_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(
            ok=True,
            data={"status": "succeeded", "summary": "Added calculator module",
                  "evidence": {"patch_text": self.patch}},
            request_id="gateway-request-1",
            provider="fake",
            model="fake-model",
        )


def _task():
    return SimpleNamespace(
        task_id="task-1", tenant_id="tenant-1", workspace_id="workspace-1",
        objective="Build a simple Python calculator application",
        acceptance_criteria=("Support four arithmetic operations", "Reject division by zero"),
    )


def _workspace(tmp_path):
    root = tmp_path / "managed"
    workspace = root / "task-1"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "KDO tests"], check=True)
    (workspace / "README.md").write_text("# Existing project\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-m", "initial"], check=True, capture_output=True)
    return root, workspace


def _patch(path="calculator.py"):
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1,2 @@\n"
        "+def add(a, b):\n"
        "+    return a + b\n"
    )


def test_gateway_builder_applies_patch_only_after_host_authorization(tmp_path):
    root, workspace = _workspace(tmp_path)
    gateway = FakeGateway(_patch())
    decisions = []
    builder = make_gateway_build_executor(
        gateway=gateway, workspace_root=str(root),
        authorize=lambda action, task: decisions.append((action, task)) or True,
    )
    result = builder(str(workspace), _task(), ("Inspect the repository", "Implement calculator"))
    assert result["status"] == "build_patch_applied"
    assert result["tests_run"] is False
    assert result["release_authorized"] is False
    assert (workspace / "calculator.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert decisions[0][0] == "build_patch"
    assert gateway.calls[0][1]["caller"] == "code_intelligence"
    assert "Stable role" in gateway.calls[0][0]


def test_gateway_builder_denied_authorization_does_not_mutate_workspace(tmp_path):
    root, workspace = _workspace(tmp_path)
    builder = make_gateway_build_executor(
        gateway=FakeGateway(_patch()), workspace_root=str(root),
        authorize=lambda *_: False,
    )
    with pytest.raises(GatewayAgentError, match="not_authorized"):
        builder(str(workspace), _task(), ("Implement calculator",))
    assert not (workspace / "calculator.py").exists()


def test_gateway_builder_rejects_traversal_patch_before_authorization(tmp_path):
    root, workspace = _workspace(tmp_path)
    called = []
    builder = make_gateway_build_executor(
        gateway=FakeGateway(_patch("../outside.py")), workspace_root=str(root),
        authorize=lambda *_: called.append(True) or True,
    )
    with pytest.raises(GatewayAgentError, match="patch_path_not_allowed"):
        builder(str(workspace), _task(), ("Implement calculator",))
    assert called == []
    assert not (tmp_path / "outside.py").exists()


def test_gateway_builder_rejects_workspace_outside_managed_root(tmp_path):
    root, workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    builder = make_gateway_build_executor(
        gateway=FakeGateway(_patch()), workspace_root=str(root),
        authorize=lambda *_: True,
    )
    with pytest.raises(GatewayAgentError, match="workspace_outside_managed_root"):
        builder(str(outside), _task(), ("Implement calculator",))
