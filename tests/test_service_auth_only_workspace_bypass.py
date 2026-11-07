from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_service_auth_only_endpoints_skip_tenant_binding_not_route_auth():
    middleware = (ROOT / "middleware/request_context_middleware.py").read_text()
    api = (ROOT / "api/v1.py").read_text()

    assert '"/api/v1/auth/exchange"' in middleware
    assert '"/api/v1/system/auth/validate"' in middleware
    assert "or _is_service_auth_only(path)" in middleware

    # The middleware bypass must not make either endpoint public. Each route
    # retains the canonical service credential dependency.
    assert "_service_auth: bool = Depends(require_auth)" in api
    assert "async def validate_service_credential(_service_auth: bool = Depends(require_auth))" in api


def test_service_auth_only_allowlist_is_exact_and_does_not_cover_subpaths():
    middleware = (ROOT / "middleware/request_context_middleware.py").read_text()
    assert "return path in SERVICE_AUTH_ONLY_EXACT" in middleware
    assert '"/api/v1/auth/exchange/"' not in middleware
    assert '"/api/v1/auth/"' not in middleware
