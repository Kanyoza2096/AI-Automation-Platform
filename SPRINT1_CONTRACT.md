# Sprint 1 — Single publish path & contract tests

## Rules enforced

1. **Director** unbound capabilities return `status=failed` / `unbound_capability` (never `"delegated"` success).
2. **Boot** logs any still-unbound non-critical capabilities after `bind_director_capabilities`.
3. **PostWorkflow PublishStep** requires `decision_id` + metabolism check before `publish_all`.
4. **`run_publish`** remains the preferred entry (API `/e2e/publish`, Director hands).

## Run tests

```bash
cd AI-Automation-Platform-latest
pytest tests/test_sprint1_publish_contract.py -v
```

## Pass criteria

- dry_run → `ok` + `decision_id`
- mocked Facebook → `ok` + plugin.publish called + bus `post.published`
- unbound Director hand → not `"delegated"`
- missing decision → authorization error
