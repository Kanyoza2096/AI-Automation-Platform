# Phase 1 — Stop-the-line fixes (2026-08-28)

## 1. Posts API → JobService only
**File:** `api/v1/posts.py`
- Removed manual `AutomationJob(...)` with invalid `job_type` / `payload` / missing `idempotency_key`
- Removed `JobRepository` usage (module never existed)
- Uses `JobService.create_manual_job(...)` with topic, decision_id, caption stored on canonical fields
- Enqueues `publish_job_task(job_id, container=container)` so the worker receives the container

## 2. Publish worker → JobService + container
**File:** `services/publish_worker.py`
- Loads jobs via `JobService.find_by_id` only
- Resolves container via argument or `get_container()`
- Explicitly issues `DIRECTOR_AUTO` **before** `assert_publish_allowed` when job has no decision_id
- Never relies on assert() to mint authority

## 3. Decision Gate strict semantics
**File:** `core/publish_authorization.py`
- `strict=True` (default via `PUBLISH_STRICT=1`): missing decision_id → **DENY**
- Auto-issue only when `strict=False` **and** `AUTO_ISSUE_PUBLISH_DECISION=1` (default now **0**)
- Matches `tests/organism/test_publish_worker_gate.py`

## 4. species_override secured
**File:** `core/decision_gate.py`
- Format required: `species_override:<SECRET>:<reason>`
- Secret must match env `SPECIES_OVERRIDE_SECRET` (HMAC compare)
- Prefix alone is rejected; missing secret config rejects all overrides

## 5. Single engine spine (DI)
**File:** `lifecycle.py`
- After boot, MetaEngine / Director / ConditionEngine registered on container
- AutonomousLoop prefers `container.try_resolve(...)` over module globals
- Globals remain as fallback only

## Also
- `workers/task_queue.py`: `async def enqueue` for coroutine workers
- `services/job_service.py`: `create_manual_job` accepts topic/decision_id/caption/result/status
- `DELETE /posts/{id}` scoped by `workspace_id`

## Env for production
```bash
PUBLISH_STRICT=1
AUTO_ISSUE_PUBLISH_DECISION=0
SPECIES_OVERRIDE_SECRET=<long random secret>
```

## Apply
Copy these files into your full repo root (overwrite):
- api/v1/posts.py
- services/job_service.py
- services/publish_worker.py
- core/publish_authorization.py
- core/decision_gate.py
- lifecycle.py
- workers/task_queue.py
