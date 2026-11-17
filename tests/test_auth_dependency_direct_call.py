from fastapi import HTTPException, Request
import pytest

from core.security.deps import require_auth_context


@pytest.mark.asyncio
async def test_direct_auth_dependency_call_reads_request_headers_instead_of_header_defaults():
    request = Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/command-center/growthbridge-articles",
        "raw_path": b"/command-center/growthbridge-articles",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    })

    with pytest.raises(HTTPException) as exc:
        await require_auth_context(request)

    # Missing credentials must fail as a normal auth response, not crash when
    # the function is called directly and FastAPI has not resolved Header(...).
    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication required"
