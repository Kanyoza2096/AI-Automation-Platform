# Kanyoza P9 — Director Container-Owned Instance Discovery

## Finding

Director discovery correctly rejected class exports to avoid arbitrary construction, but it also skipped classes whose live singleton had already been created by the selected boot mode and stored in the dependency-injection container. This left real services such as the AI orchestrator, plugin registry, or scheduler invisible to Director even when their owning lifecycle had initialized them.

## Change

- `services/director/core.py`
  - When an engine-registry export is a class, Director now asks the container's instance-only `try_get_instance()` API for an already-created singleton.
  - It registers that instance only if it exists; it never falls back to `try_resolve()` and never constructs the class during discovery.
  - Arbitrary callable/request-handler exports remain rejected unless they are in the existing explicit getter allowlist.
  - The discovery report still records classes that have no live instance as unsupported exports.
- Added a regression test proving an existing container-owned instance is discovered, while lazy resolution is never called.

## Validation

- Compilation of changed module and tests: passed.
- Focused Director / topology / route / health-truth suites: **115 passed**.
- The full suite remains uncertified green due to the known environment failure in the publishing contract test (`No module named 'supabase'`).

## Expected effect

Director can observe more real engines when their lifecycle has already created them, without waking optional services in restricted boot modes. The discovery report remains the source of truth for classes whose live instances are still missing.
