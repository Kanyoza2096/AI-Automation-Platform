# Hotfix — API v1 routes not mounting (control plane timeout)

## Root cause
Log: `API v1 routes failed to mount: module 'api.v1.router' has no attribute 'routes'`
(actual getattr: package `api.v1` had **no** `router` attribute)

Directory `api/v1/` **shadows** file `api/v1.py` (57KB monolith with `/api/v1/plugins`, brands, etc.).
`main` did `from api.v1 import router` → package without router → **control plane 404/empty**.

## Fix
- `api/v1/__init__.py` loads monolith via importlib and exports `router`
- Modular `router.py` includes plugins_api + more as backup
- `main` warns clearly if still missing

## After deploy
Logs should show: `API v1 routes mounted.`
Then control plane `/api/v1/plugins` can respond (with auth).
