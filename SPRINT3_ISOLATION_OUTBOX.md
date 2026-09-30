# Sprint 3 — Tenant isolation + durable outbox

## Isolation

- `core/tenant_guard.py` — `assert_workspace_access`, `filter_rows_for_workspace`
- Outbox `get` / `list_for_workspace` refuse cross-tenant reads
- Migration `017_publish_outbox.sql` + RLS policy on `app.workspace_id`

## Durable publish

```
run_publish(..., durable=True)
  → decision + caption
  → publish_outbox.enqueue (status=pending)
  → return {queued: true, outbox_id}
  → drainer claims → run_publish(_from_outbox=True) → Graph/plugin
```

Survive process restart when Supabase `publish_outbox` is applied (in-memory works within process).

## APIs

| Method | Path | Who |
|--------|------|-----|
| GET | `/api/v1/outbox/{workspace_id}` | workspace member / species |
| GET | `/api/v1/outbox/{workspace_id}/item/{id}` | same tenant only |
| POST | `/api/v1/outbox/drain` | species only |

```bash
curl -X POST "$HOST/api/v1/e2e/publish" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","caption":"Hi","dry_run":false,"durable":true}'
```

## Tests

```bash
pytest tests/test_sprint3_isolation_outbox.py -v
```

Apply SQL: `migrations/017_publish_outbox.sql`
