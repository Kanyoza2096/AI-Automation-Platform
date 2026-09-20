# Phase 37.11 — Complete Route Wiring Audit

## Inventory
- Static route definitions discovered: **311**
- Duplicate method/path pairs: **0**
- Parse errors: **0**
- Flagged handlers requiring review: **3**

## Duplicate routes
None.

## Flagged routes
- `GET /e2e/director-hands` — `api/e2e_routes.py:65` — markers: placeholder
- `GET /api/v1/notifications` — `api/v1.py:5238` — markers: placeholder
- `POST /api/v1/circulation/check` — `routes/circulation_routes.py:102` — markers: not implemented

## Interpretation
- The route inventory is static and covers `api/`, `routes/`, and `plugins/` without importing optional runtime dependencies.
- Zero duplicate method/path pairs remain after removing compatibility shadows.
- Required backend contract paths are protected by regression tests.
- Timeline routes are explicitly accounted for because `main.py` mounts their router under `/api/v1`.
- A flagged route is not automatically broken; it is a review signal for placeholder/fail-closed text.
- Real DB/RLS, Redis, provider APIs, and Docker runtime still require staging evidence.
