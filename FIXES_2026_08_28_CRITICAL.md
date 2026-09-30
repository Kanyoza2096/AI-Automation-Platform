# Critical fixes applied 2026-08-28

## 1. API v1 monolith load (NameError chain)
**Problem:** `api/v1.py` failed to load (`NameError: get_key_store` / `create_rate_limit_dependency`) because extracted modules referenced deps that were never injected. A stray module-level `get_key_store` stub was also inserted in the middle of `create_app()`, making CORS + `include_router(router)` unreachable.

**Fixes:**
- Added `_v1_common_deps()` and pass `**_v1_common_deps()` into modular `register_dependencies` calls.
- Wrapped every `_mount_*_routes()` invocation in try/except so one failure does not abort the whole monolith.
- Removed the stray `get_key_store` stub that broke `create_app()`.

## 2. Modular routers hardened
**Problem:** Routes only registered inside `register_dependencies`; missing deps → empty routers → empty control plane.

**Fixes:** Extract optional deps via `deps.get(...)` with safe fallbacks for:
- `get_key_store`, `get_workflow_history`, `require_workspace_ownership`
- `create_rate_limit_dependency`, `_get_resource_workspace_id`, `_strip_credentials`
- `import threading` in `system_ops.py`
- Nested mounts in `ai_runtime.py` / `bot.py` wrapped
- `api/v1/router.py` modular builder passes a full stub deps dict

## 3. Missing `ensure_social_plugins`
**Problem:** `lifecycle.py` imported a non-existent symbol → social plugin ensure failed at boot.

**Fix:** Implemented `ensure_social_plugins(container)` in `services/publish_orchestration.py`.

## 4. Growthbridge `KeyError: 'intent'`
**Problem:** `CommandExecutor.register_external_command` required `command['intent']`; Growthbridge only had `name`.

**Fixes:**
- Growthbridge command dict includes `"intent": "growthbridge_stats"`.
- `CommandExecutor` accepts either `intent` or `name` and normalizes.

## Expected after deploy
- Log: `API v1 routes mounted.`
- Monolith loads without process-killing NameError
- Growthbridge boot no longer fails on `'intent'`
- Social plugin ensure no longer ImportError

## Still ops-dependent
- Supabase, Gemini, Facebook tokens, Redis for full functionality
