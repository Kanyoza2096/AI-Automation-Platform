# Kanyoza P26 — Safe End-to-End Staging Verification

## Objective
Exercise the Director's local in-process workflow across capability inventory, binding, safe diagnostic execution, governance enforcement, and recovery safety, without contacting production services or triggering external side effects.

## Changes

- Added `tests/test_phase26_staging_workflow.py` covering the local staged workflow:
  - capability inventory and binding evidence;
  - safe `self_diagnostic` handler invocation and returned status evidence;
  - refusal to directly execute proposal-capable `scale_worker_pool` without the governed proposal handoff;
  - unknown outcome reconciliation before retry.
- Added `scripts/verify_director_staging.py`, a reusable side-effect-free smoke check that emits machine-readable JSON and exits nonzero when an expected contract fails.
- Smoke-check output explicitly reports that live dependencies remain unverified and that no production actions were invoked.

## Validation

- Focused Director and P16–P26 regression suite: **163 passed**.
- Python compilation for relevant Director modules, test, and smoke script: passed.
- Local staging smoke check: **4/4 contract checks passed**.
- ZIP integrity: verified after packaging.

## Scope and limitations

This is an in-process staging contract check, not a live end-to-end deployment test. It does not contact Supabase, Redis, production service adapters, external APIs, or real worker pools. It cannot establish production dependency health, cross-service event delivery, durable persistence, or real-world outcome verification. Those checks require deployment-specific staging credentials and an isolated staging environment.

## Run the smoke check

From the repository root:

```bash
python scripts/verify_director_staging.py
```

## Files changed

- `tests/test_phase26_staging_workflow.py`
- `scripts/verify_director_staging.py`
- `KANYOZA_P26_END_TO_END_STAGING_VERIFICATION_REPORT.md`
