# HARDEN P0 — Queue / Lock / Workspace (architecture report §§20–22)

## TaskQueue (`workers/task_queue.py`)
- **Before:** queue full → drop task → still return `task_id` (fake success)
- **After:**
  - `try_submit()` → `task_id | None`
  - `submit()` → `task_id` or raise `QueueFullError`
  - stats include `rejected`

## DistributedLock (`core/distributed_lock.py`)
- Modes: `distributed` | `degraded_local` | `unavailable`
- Redis preferred; local fallback is **explicitly DEGRADED_LOCAL**
- `require_distributed=True` or `REQUIRE_DISTRIBUTED_LOCK=1` → no local pretend
- Scheduler uses this module and logs degraded mode

## WorkspaceContext (`core/workspace_context.py`)
- Existing law kept: X-Workspace-ID is not authority alone
- Added `require_workspace_context(allow_default=False)` — fail closed
- Added `assert_workspace_matches(resource_workspace_id)` — defense in depth

## Env
- `REQUIRE_DISTRIBUTED_LOCK=true` — multi-instance publish only with Redis

## Still open HARDEN
- RLS migrations verification
- Webhook signature + idempotency
- Blocking requests → httpx in remaining paths
- Full API middleware wiring of require_workspace_context
