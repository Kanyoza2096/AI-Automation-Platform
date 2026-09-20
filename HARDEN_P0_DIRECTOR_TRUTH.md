# HARDEN P0 — Director truthful outcomes (report §7, §40)

## Rules
- No capability may report success unless the underlying action succeeded
- Placeholder handlers return `ok: false`, `status: unavailable`
- Critical handlers remain `blocked`

## Changes
### `services/director.py`
- `_placeholder` → explicit failure payload (`handler_not_bound`)
- `_handler_result_ok()` — rejects delegated/unavailable/blocked/error
- `execute_capability` — refuses lambda/placeholder; `ok` follows result truth
- `_execute_async` — marks task **FAILED** when result is not truthful success

### `services/director_bindings.py`
- Logs unbound capability names after bind
- Disables auto-execution for still-placeholder non-critical caps

## Operators
After deploy, check logs for:
`Director unbound capabilities (will not report success until bound): [...]`
Bind remaining hands via `bind_director_capabilities` / service container.
