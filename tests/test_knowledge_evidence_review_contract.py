import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "api" / "routers" / "knowledge.py"
MIGRATION_PATH = ROOT / "migrations" / "172_knowledge_evidence_review_governance.sql"


def _route_contracts(source: str) -> set[tuple[str, str]]:
    tree = ast.parse(source)
    contracts = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
                continue
            if func.value.id != "router" or func.attr not in {"get", "post"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                contracts.add((func.attr.upper(), decorator.args[0].value))
    return contracts


def test_review_api_exposes_submit_list_and_human_decision_routes():
    source = API_PATH.read_text()
    routes = _route_contracts(source)
    assert (
        "POST",
        "/brands/{brand_id}/knowledge/{knowledge_id}/evidence-review",
    ) in routes
    assert (
        "GET",
        "/brands/{brand_id}/knowledge-evidence-reviews",
    ) in routes
    assert (
        "POST",
        "/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/decision",
    ) in routes


def test_review_api_uses_server_service_client_and_never_promotes_verified_flag():
    source = API_PATH.read_text()
    assert 'getattr(supabase_service, "service_client", None)' in source
    assert 'supabase_service.client' not in source
    assert "evidence_promoted" in source
    assert '"evidence_promoted": False' in source
    assert "metadata.verified" not in source


def test_database_guard_binds_case_to_locked_current_content_and_tenant():
    migration = MIGRATION_PATH.read_text()
    assert "FOR UPDATE" in migration
    assert "extensions.digest(convert_to(current_content, 'UTF8'), 'sha256')" in migration
    assert "current_brand_id IS DISTINCT FROM NEW.brand_id" in migration
    assert "current_workspace_id IS DISTINCT FROM NEW.workspace_id" in migration
    assert "Approved evidence review cases are immutable" in migration


def test_review_approval_requires_human_identity_and_stable_source():
    source = API_PATH.read_text()
    assert "require_human: bool = True" in source
    assert "A human reviewer identity is required" in source
    assert "Approval requires a stable source ID" in source
    assert "Approval requires an HTTP(S) source URL" in source
