from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_service_auth_only_endpoints_skip_tenant_binding_not_route_auth():
    middleware = (ROOT / "middleware/request_context_middleware.py").read_text()
    api = (ROOT / "api/v1.py").read_text()

    assert '"/api/v1/auth/exchange"' in middleware
    assert '"/api/v1/auth/workspace"' in middleware
    assert '"/api/v1/system/auth/validate"' in middleware
    assert "or _is_service_auth_only(path)" in middleware

    # Middleware bypasses tenant binding only. FastAPI routes retain service auth.
    assert "_service_auth: bool = Depends(require_auth)" in api
    assert "async def validate_service_credential(_service_auth: bool = Depends(require_auth))" in api
    assert "async def auth_workspace(" in api
    assert "async def auth_exchange(" in api


def test_service_auth_only_allowlist_is_exact_and_does_not_cover_subpaths():
    middleware = (ROOT / "middleware/request_context_middleware.py").read_text()
    assert "return path in SERVICE_AUTH_ONLY_EXACT" in middleware
    assert '"/api/v1/auth/exchange/"' not in middleware
    assert '"/api/v1/auth/"' not in middleware


def test_gateway_owned_auth_never_accepts_browser_supabase_tokens():
    api = (ROOT / "api/v1.py").read_text()
    assert "email: str = Field(min_length=3, max_length=320)" in api
    assert "password: str = Field(min_length=1, max_length=1024)" in api
    assert "grant_type=password" in api
    assert '"apikey": settings.supabase_anon_key' in api
    assert "payload.access_token" not in api
    assert "gateway_password_auth" in api
