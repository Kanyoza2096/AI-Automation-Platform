# HARDEN P0 — Tenancy API + RLS (architecture report §§22–23)

## Middleware (`middleware/request_context_middleware.py`)
- Non-species: **X-Workspace-ID is ignored** (logged if mismatched)
- Species: optional X-Workspace-ID with basic validation only
- Empty workspace on non-species credentials → **403**

## FastAPI deps (`core/security/deps.py`)
- `require_workspace` → trusted workspace_id from bound context / auth
- `assert_resource_workspace` → 403 on mismatch

Usage:
```python
from core.security.deps import require_workspace, assert_resource_workspace

@router.get("/items")
async def list_items(workspace_id: str = Depends(require_workspace)):
    ...
```

## Migration `018_rls_defense_in_depth.sql`
Enable RLS + workspace isolation policies for:
- events, automation_jobs, trend_decisions, publish_outbox
(and ENABLE on related tables)

**Apply in Supabase SQL editor.** service_role still bypasses RLS — app must scope by SpeciesAuth workspace.

## Prior P0 (same ZIP)
- TaskQueue truthful accept/reject
- DistributedLock DEGRADED_LOCAL honesty
- require_workspace_context / assert_workspace_matches
