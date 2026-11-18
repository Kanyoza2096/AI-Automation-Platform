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
    assert 'metadata["verified"]' not in source
    assert '"verified": True' not in source
    assert '"verified": true' not in source


def test_database_guard_binds_case_to_locked_current_content_and_tenant():
    migration = MIGRATION_PATH.read_text()
    assert "FOR UPDATE" in migration
    assert "digest(convert_to(current_content, 'UTF8'), 'sha256')" in migration
    assert "current_brand_id IS DISTINCT FROM NEW.brand_id" in migration
    assert "current_workspace_id IS DISTINCT FROM NEW.workspace_id" in migration
    assert "Approved evidence review cases are immutable" in migration


def test_review_approval_requires_human_identity_and_stable_source():
    source = API_PATH.read_text()
    assert "require_human: bool = True" in source
    assert "A human reviewer identity is required" in source
    assert "Approval requires a stable source ID" in source
    assert "Approval requires an HTTP(S) source URL" in source


def test_candidate_submission_can_be_automated_but_decisions_require_a_human():
    source = API_PATH.read_text()
    submit_start = source.index("async def submit_knowledge_evidence_review")
    list_start = source.index("async def list_knowledge_evidence_reviews")
    decision_start = source.index("async def decide_knowledge_evidence_review")
    assert "_evidence_review_actor(require_human=False)" in source[submit_start:list_start]
    assert "_evidence_review_actor(require_human=False)" in source[list_start:decision_start]
    assert "_evidence_review_actor(require_human=True)" in source[decision_start:]


def test_approved_source_metadata_cannot_be_rewritten():
    migration = MIGRATION_PATH.read_text()
    guard = migration[migration.index("CREATE OR REPLACE FUNCTION public.guard_knowledge_evidence_review_case"):]
    for field in ("source_id", "source_url", "source_title", "source_type", "trust_class"):
        assert f"NEW.{field} IS DISTINCT FROM OLD.{field}" in guard


def test_promotion_is_explicit_human_authorized_and_transactional():
    source = API_PATH.read_text()
    routes = _route_contracts(source)
    assert (
        "POST",
        "/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/promote",
    ) in routes
    assert "promote_approved_knowledge_evidence" in source
    assert "_evidence_review_actor(require_human=True)" in source


def test_promotion_rpc_is_audited_idempotent_and_content_changes_revoke():
    migration = ROOT / "migrations" / "173_knowledge_evidence_promotion.sql"
    source = migration.read_text()
    assert "FOR UPDATE" in source
    assert "knowledge_evidence_promotion_case_event_unique" in source
    assert "metadata_after" in source and "'verified', true" in source
    assert "knowledge_evidence_content_invalidate" in source
    assert "'verified', false" in source
    assert "system:knowledge-content-change" in source
    assert "GRANT EXECUTE ON FUNCTION public.promote_approved_knowledge_evidence(UUID, TEXT)" in source


def test_database_rejects_malformed_source_urls_and_supersedes_unpromoted_cases():
    review_migration = MIGRATION_PATH.read_text()
    promotion_migration = (ROOT / "migrations" / "173_knowledge_evidence_promotion.sql").read_text()
    assert "^https?://[^/[:space:]?#]+" in review_migration
    assert "^https?://[^/[:space:]?#]+" in promotion_migration
    assert "review_status IN ('pending', 'approved')" in promotion_migration
    assert "content_sha256 IS DISTINCT FROM current_hash" in promotion_migration


def test_provisional_entry_ids_cannot_be_approved_as_source_provenance():
    api_source = API_PATH.read_text()
    migration = MIGRATION_PATH.read_text()
    assert 'body.source_id.strip().startswith("unverified-entry:")' in api_source
    assert "source_id NOT LIKE 'unverified-entry:%'" in migration


def test_security_definer_functions_pin_search_path_and_qualify_digest():
    review = MIGRATION_PATH.read_text()
    promotion = (ROOT / "migrations" / "173_knowledge_evidence_promotion.sql").read_text()
    for source in (review, promotion):
        assert "SET search_path = pg_catalog" in source
        assert "extensions.digest(convert_to(" in source
        assert "SET search_path = public" not in source


def test_review_state_transitions_require_audited_reviewer_identity():
    review = MIGRATION_PATH.read_text()
    assert "Pending evidence cases may only change through a decision or supersession" in review
    assert "Evidence review decisions require reviewer identity and timestamp" in review
    assert '"reviewed_by": actor' in (ROOT / "api" / "routers" / "knowledge.py").read_text()


def test_review_submission_and_decision_are_concurrency_safe():
    source = API_PATH.read_text()
    assert "concurrent submitter may have won the unique" in source
    assert "raced.data[0]" in source
    assert '.eq("id", case_id).eq("review_status", "pending").select("*").execute()' in source
    assert '_ok({"review_case": _evidence_review_payload(changed.data[0])' in source


def test_migrations_have_single_definitions_and_exact_extension_qualification():
    review = MIGRATION_PATH.read_text()
    promotion = (ROOT / "migrations" / "173_knowledge_evidence_promotion.sql").read_text()
    assert review.count("CREATE OR REPLACE FUNCTION public.") == 3
    assert promotion.count("CREATE OR REPLACE FUNCTION public.") == 3
    assert "extensions.extensions.digest" not in review + promotion
    assert "SET search_path = public" not in review + promotion


def test_evidence_audit_ledgers_revoke_service_role_mutation_privileges():
    migration = (ROOT / "migrations" / "174_knowledge_evidence_audit_append_only_privileges.sql").read_text()
    assert "FROM PUBLIC, anon, authenticated, service_role" in migration
    assert "GRANT SELECT, INSERT" in migration
    assert "UPDATE" not in migration
    assert "DELETE" not in migration
    assert "TRUNCATE" not in migration
