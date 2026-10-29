# Kanyoza P5 — Canonical Engine Name Alignment

## Finding

The Director's internal `ENGINE_REGISTRY` labels its own singleton `meta_director`, while the service lifecycle and `CANONICAL_ENGINE_DEPENDENCIES` use `director`. Meta Engine consumed that display label as a runtime key, so it could register the same Director instance under a non-canonical extra name and report topology drift.

## Change

- Added a narrow alias normalization at the Meta Engine registration boundary: `meta_director` maps to canonical runtime node `director`.
- Director's own observer registry is unchanged; its internal label remains `meta_director` where it serves a distinct purpose.
- Added `normalized_aliases` to the Meta Engine registration report so normalization is visible and auditable.
- Added a regression test proving that the live instance registers under `director`, not `meta_director`.

## Validation

- `compileall` for the changed Meta Engine module and test file: passed.
- Focused Director / topology / route / health-truth suites: **108 passed**.
- Full-suite run previously reached 655 passing tests before stopping on the first unrelated environment failure: publishing contract test requires the `supabase` package, which is absent (`No module named 'supabase'`). Full suite is not certified green.

## Boundary

This fixes one verified canonical-name mismatch only. It does not fabricate missing canonical nodes or claim that the remaining topology is complete. Missing runtime services must be resolved from their real owning lifecycle/container, then verified in the live `/topology` report.
