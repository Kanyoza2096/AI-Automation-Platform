# Hotfix — after deploy: get_key_store + API mount

## Logs
```
Failed to load api/v1.py monolith: name 'get_key_store' is not defined
API v1 routes failed to mount: ...
Gemini HTTP 429 rate limited
```

## Cause
`api/v1/knowledge.py` nested `_mount_keys_routes()` referenced `get_key_store` without it being injected → monolith load aborted → no control plane routes.

## Fixes
1. Removed nested keys mount from knowledge.py
2. Pass `get_key_store` into knowledge deps from v1.py
3. Package always falls back to **modular** routers if monolith fails

## Gemini 429
Not fixed by API mount — set on Render:
- Remove `TREND_USE_LLM`, `TREND_SEMANTIC_SCORE`, `TREND_LLM_BRIEFING`
- Or `TREND_DETERMINISTIC=1`
- Content engine may still call Gemini; rate limit is quota

## Expect after deploy
`API v1 routes mounted.` or `API v1 using modular routers`
