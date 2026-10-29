# HARDEN P0 — Tenant scope + SSRF + Async HTTP

## Tenant leaks closed
- `GET /api/v1/brands` — scoped to trusted workspace (no cross-tenant `find_all`)
- `GET /api/v1/workspaces` — non-species cannot dump all workspaces

## SSRF (`core/ssrf_guard.py`)
- Blocks private/loopback/link-local/metadata hosts
- DNS resolve check
- Monitor plugin validates URL before fetch; `allow_redirects=False`

## Async HTTP (`utils/http_client.py`)
- `AsyncHttpClient` via **httpx** for async routes
- Sync `HttpClient` (requests) remains for worker threads only

## Next
- Migrate high-traffic async callers from `requests` to `AsyncHttpClient`
- Remaining connector SSRF where user-supplied URLs exist
