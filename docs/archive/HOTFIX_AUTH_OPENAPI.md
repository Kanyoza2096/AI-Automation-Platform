# Hotfix — Auth workspace injection + OpenAPI ForwardRef (2026-08-28)

## Auth / workspace
**Root cause:** Master/species tokens set `workspace_id=None`. Middleware then set response header `X-Workspace-ID` to `None` → Starlette TypeError → `"Authentication pipeline failure"`.

**Fixes:**
- `middleware/request_context_middleware.py` — species operators use `X-Workspace-ID` or `default_workspace_id` / `"platform"`; always bind a real string; sync `auth_workspace_id_var`; never set None headers
- `api/v1.py` `require_auth` — same effective workspace resolution for Depends path

## OpenAPI
**Root cause:** Modular routers under `from __future__ import annotations` store body types as ForwardRefs resolved from module globals; models were not injected into those globals.

**Fixes:**
- `_v1_common_deps()` includes all Pydantic request models
- `api/v1/posts.py` publishes models to module globals
- `_finalize_modular_openapi_models()` runs after mounts

**Verified:** `app.openapi()` → 257 paths; PostCreateRequest and related schemas present.
