"""Phase 4 architectural boundary contracts for the HTTP layer."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_v1_uses_canonical_dependency_module() -> None:
    v1 = (ROOT / "api" / "v1.py").read_text(encoding="utf-8")
    deps = (ROOT / "api" / "dependencies.py").read_text(encoding="utf-8")
    assert "from api.dependencies import (" in v1
    assert "async def require_auth(" in deps
    assert "async def require_auth(" not in v1
    assert "def create_rate_limit_dependency(" not in v1
    assert "async def require_workspace_ownership(" not in v1


def test_legacy_route_modules_keep_compatibility_without_own_auth_implementations() -> None:
    for relative in (
        "api/metabolism_routes.py",
        "api/timeline_routes.py",
        "api/organism_body_routes.py",
        "routes/autonomy_routes.py",
        "routes/cp_surface_routes.py",
        "routes/email_routes.py",
        "routes/backend_contract_routes.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from api.v1 import require_auth" in source or "from api.v1 import" in source


def test_v1_no_longer_owns_request_auth_context_state() -> None:
    names = _function_names(ROOT / "api" / "v1.py")
    assert "require_auth" not in names
    assert "require_workspace_ownership" not in names
    assert "create_rate_limit_dependency" not in names
