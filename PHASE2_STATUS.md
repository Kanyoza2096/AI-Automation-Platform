# Phase 2 — Content path E2E — DONE

## Goal

Generate → render → publish → job status must be callable from the async runtime without nested-loop crashes, and job transitions must persist.

## Fixes

| Issue | Fix |
|---|---|
| `asyncio.run` inside pipeline steps under ASGI | `_run_coro_sync` — raises clear error if loop running; steps use `run_async` |
| `ContentGenerationPipeline.run()` returned Future in async context | Raises: use `await execute_async()` |
| `self.steps` missing on Workflow | `@property steps` → `_steps` |
| brand_scheduler called `workflow.run()` + expected `.steps` objects | `await execute_async({})` + `_parse_pipeline_result` |
| JobService missing `mark_published` / `mark_failed` | Added (async, via `transition`) |
| Job methods called without await | All `transition` / `mark_*` / `create_*` awaited |
| Dual JobStatus exports | `models.JobStatus` = automation pipeline; `SchedulerJobStatus` for scheduler slots |
| Real publish in local tests | `context["dry_run"]=True` skips social publish |

## Canonical path

```python
pipeline = factory(brand=brand, ai_profile=profile)
result = await pipeline.execute_async({"dry_run": True})  # or {} for real publish
# result = {"status": "completed"|"failed", "steps": [...], ...}
```

Scheduler:

```python
result = await workflow.execute_async({})
topic, publish_ok, platforms = _parse_pipeline_result(result)
await job_service.mark_published(job, result={...})  # or mark_failed
```

## Still depends on env for *real* publish

- Gemini key for LLM stage
- Playwright for card render (stage continues on failure)
- Facebook (or other) plugin tokens for non-dry_run publish
- Supabase for durable AutomationJob rows

## Next: Phase 3

Wire Director / Meta / Condition / Council so engines call this path with real handlers.
