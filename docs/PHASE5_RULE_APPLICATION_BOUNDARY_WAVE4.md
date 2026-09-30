# Phase 5 — Rule Application Boundary (Wave 4)

## Scope
Extract the Rule Engine HTTP use cases from `routes/rule_routes.py` into an application service while preserving the existing Condition Engine read model and rule behavior.

## Boundary

```text
HTTP / FastAPI
    ↓
routes/rule_routes.py
    ↓
RuleManagementApplicationService
    ↓
RuleStorePort / ConditionEnginePort
    ↓
In-memory compatibility store / Condition Engine adapter
```

The route remains responsible for HTTP translation, validation models, authentication dependency wiring, and HTTP error mapping. The application layer owns rule CRUD, toggling, dry-run evaluation, history, metrics, and health use cases.

## Invariants
- No FastAPI, Starlette, or Pydantic dependency in `application/rules.py`.
- No direct Condition Engine internals in the HTTP route.
- Existing rule-store compatibility is preserved.
- Condition Engine remains an infrastructure adapter, not an application dependency leak.
- No new external execution authority is introduced.

## Validation
- Phase-5 boundary suite: **17/17 passed**.
- Architecture + plugin-security suite: **53/53 passed**.
- Execution/reliability targeted regression: **36/36 passed**.
- Full `tests/reliability` run exceeded the 120-second harness timeout; this is recorded as a timeout, not treated as a pass or failure.
- Python compilation: PASS.
