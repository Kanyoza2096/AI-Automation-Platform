# Phase D gaps closed

## Fixes applied

1. **Migration structure + bootstrap**
   - `scripts/bootstrap_migrations.py` — structure gate + real apply when `DATABASE_URL` set
   - `009_automation_jobs.sql` — no `workspace_id DEFAULT ''`, `job_id UNIQUE`, idempotency comment matches `UNIQUE(idempotency_key)`

2. **Canonical pipeline hard gates**
   - Guardian: real evaluate when bound; otherwise **warn** (not silent soft-allow)
   - Council: real deliberate when orchestrator bound; `require_council=True` fails closed
   - Metabolism: `MetabolismError` deny; unknown error **fail closed** for medium+ risk / non-dry-run
   - Outbox enqueue failure when `durable=True`: **FAIL**, no simulated publish
   - Outcome recorded **only after** connector execution (not after queue)
   - Empty `workspace_id` rejected
   - Typed `PipelineStage` (started/completed/duration/error_code)

3. **Tests**
   - Outbox failure does not simulate success
   - Empty workspace rejected
   - Trace typed fields
   - Stage order includes council

## Still needs your environment

- `DATABASE_URL=... python scripts/bootstrap_migrations.py` on empty Postgres
- Live connector tokens for non-simulated publish
- RLS validation against live schema (policy SQL already in migrations)

## Run

```bash
python scripts/bootstrap_migrations.py --structure-only
pytest tests/failure tests/pipeline tests/security tests/organism tests/tenancy -v
```
