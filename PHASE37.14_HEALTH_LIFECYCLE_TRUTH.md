# Phase 37.14 — Health & Organism Lifecycle Truth

## Objective

Remove false `UNKNOWN`/unreported states caused by missing lifecycle startup and a broken
system-health dependency construction, while preserving fail-closed semantics for genuinely
unobservable infrastructure.

## Changes

### 1. Authenticated workspace organism lifecycle

`middleware/request_context_middleware.py` now calls the canonical
`organism_registry.ensure_started(workspace_id)` after authentication and workspace binding.

Creation is no longer treated as life. The request records:

- `ALIVE` when the workspace organism runtime is successfully started.
- `UNOBSERVABLE` when startup cannot be verified.

Startup failure does not silently claim health and does not turn the authentication pipeline
into an HTTP 500.

### 2. Explicit organism life truth

`Organism.vitals()` now exposes:

- `running`
- `life_status`: `ALIVE` or `STOPPED`
- `life_status_basis`

If no runtime exists, `/organism/vitals` reports `NOT_BORN` rather than overloading
`running=false` as a health verdict.

`ALIVE` means the organism runtime has been started in the current application process.
It does **not** mean Redis, database, providers, or external side effects are healthy.

### 3. System-health dependency fix

`/api/v1/system-health` previously constructed `HealthChecker()` without its required
registry dependency. It now uses the real `plugins.base.get_plugin_registry()` and returns
an explicit health-check observation envelope containing:

- checker status
- number of plugins checked
- individual results

Exceptions remain represented as `unobservable`.

### 4. Canonical release identity

Platform version advanced to `14.7.5`. API status now consumes the canonical
`PLATFORM_VERSION` constant instead of a duplicated literal.

## Validation

Targeted lifecycle/health tests: **3 passed**.

Organism architecture/version contract tests: **11 passed**.

A full-suite run reached **231 passed / 7 failed / 1 warning** in the current execution
environment. The seven failures are not introduced by these changes: five are existing
failure-matrix/pipeline expectations and two route-audit subprocess tests fail because the
environment's stdout path raises `BlockingIOError` while the audit scripts emit large JSON
inventories. The production code changes in this phase compile successfully.

## Truth model

The Command Center should interpret the new fields as:

`ALIVE` → organism runtime lifecycle observed.

`STOPPED` → organism runtime explicitly stopped.

`NOT_BORN` → no organism runtime exists for that workspace.

`UNOBSERVABLE` → lifecycle could not be verified.

None of these states are substitutes for dependency/provider execution health.
