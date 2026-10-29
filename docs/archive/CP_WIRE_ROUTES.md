# Control Plane wire routes (gap closure)

## Added packages under `routes/`

| Module | Prefix | CP endpoints covered |
|--------|--------|----------------------|
| `circulation_routes.py` | `/api/v1/circulation` | status, snapshot, blockages, history, check, heal/{id} |
| `pulse_routes.py` | `/api/v1/pulse` | status, tables, stagnated, alerts |
| `meta_routes.py` | `/api/v1/meta` | status, engines, diagnoses, diagnose, heal, incidents, resolve, dependency-graph |
| `cp_gap_routes.py` | `/api/v1` | alerts, rules, events, condition/health, predictions/*, quality/*, competitors/*, applications, repos, services, topology, council/vote |

## Mounted from `main.py`

Alongside existing circulation mount:
- `register_pulse_routes`
- `register_meta_routes`
- `register_cp_gap_routes`

## Semantics

- Circulation/Pulse: bind to live monitors when lifecycle started; otherwise `status: unavailable` (honest empty, not fake health).
- Meta / predictions / competitors: thin surfaces so CP pages do not 404; extend with real engines later.
- `/council/vote`: accepted alias; full deliberation remains `/api/v1/council/decisions`.
