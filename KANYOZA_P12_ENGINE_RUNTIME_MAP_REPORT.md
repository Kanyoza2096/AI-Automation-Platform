# Kanyoza P12 — Engine Runtime Map

## Deliverable

Added a reproducible source inventory:

- `scripts/audit_engine_topology.py`
- `docs/KANYOZA_ENGINE_RUNTIME_MAP.md`

The inventory joins the 34 canonical engine/provider nodes with their declared dependencies, service-definition resolver (where present), Director discovery export (where present), and source-mapped lifecycle boot owner/modes. It normalizes the Director `meta_director` observer alias to canonical `director`.

## Design boundary

The script reads source declarations only. It does not start services, run health checks, invoke arbitrary factories, or claim that a service is present/healthy. The document directs operators to `/api/v1/topology` and `/api/v1/system-health` for runtime evidence. Missing direct service definitions are marked as ownership/registration gaps to verify, not as proof a component is broken.

## Validation

- `python scripts/audit_engine_topology.py --output docs/KANYOZA_ENGINE_RUNTIME_MAP.md`: passed.
- Compilation of the audit script, topology module, and tests: passed.
- Focused Director / topology / route / health-truth suites: **118 passed**.
- Full repository suite remains uncertified green due to the known missing `supabase` package in the publishing pipeline test environment.

## Operational value

This gives the project a concrete engine map to compare with daily health reports. In particular, it distinguishes canonical dependencies, declared lifecycle ownership, Director discovery coverage, and live health—which must remain separate concepts.
