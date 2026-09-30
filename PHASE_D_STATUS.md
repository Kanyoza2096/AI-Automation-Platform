# Phase D — Metabolism — COMPLETE (code)

Goal: plans as biological limits; usage metered; Stripe optional for upgrades.

## Checklist

| Item | Status | Location |
|------|--------|----------|
| Plan catalog (free/pro/business/enterprise) | Done | `core/metabolism.py` |
| Usage meter (posts/day, AI tokens/month) | Done | `UsageMeter` |
| Enforce before publish | Done | `publish_authorization` + `assert_can_publish` |
| Enforce before AI budget | Done | `assert_can_think` / `consume_ai` |
| Industry organ lock by plan | Done | `assert_organ_allowed` |
| Apply plan → living Organism | Done | `apply_plan_to_organism` |
| Stripe Checkout + webhook | Done (optional) | `StripeBilling` |
| HTTP API | Done | `api/metabolism_routes.py` |
| Migration | Done | `migrations/014_metabolism.sql` |
| Lifecycle wires meter | Done | `lifecycle.py` |
| Tests | Done | `tests/test_phase_d_metabolism.py` |

## Limits (default catalog)

| Plan | Posts/day | AI tokens/mo | Brands | Director |
|------|-----------|--------------|--------|----------|
| free | 5 | 50k | 1 | suggest |
| pro | 20 | 500k | 5 | auto_low |
| business | 100 | 2M | 25 | auto_medium |
| enterprise | 1000 | 20M | 100 | auto_medium |

## API

- `GET  /api/v1/metabolism/plans`
- `GET  /api/v1/metabolism/status`
- `POST /api/v1/metabolism/checkout`  (Stripe)
- `POST /api/v1/metabolism/webhook/stripe`
- `POST /api/v1/metabolism/admin/set-plan`  (species only)

## Env

```
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_PRO=
STRIPE_PRICE_BUSINESS=
```

## Operator

1. Apply `014_metabolism.sql`
2. Publish path:

```python
from core.publish_authorization import assert_publish_allowed
assert_publish_allowed(job.decision_id, job.workspace_id, consume=True)
```

3. AI path:

```python
from core.metabolism import metabolism
metabolism.consume_ai(workspace_id, tokens_used)
```

## Next: Phase E

Body visible — vitals/mind/hands/sleep endpoints from real bus + organism census.
