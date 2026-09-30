# Phase 37.12 — Route-to-Service Wiring Audit

## Scope
Static audit of all FastAPI router modules under `api/`, `routes/`, and `plugins/`, plus the runtime mount declarations in `main.py`. The audit intentionally does not import the application, so optional dependencies cannot hide missing route wiring.

## Results
- Route definitions: **311**
- Router-bearing modules: **30**
- Orphan router modules: **0**
- Parse errors: **0**
- Duplicate method/path pairs: **0** (carried forward from Phase 37.11)
- Handlers with observable service/repository/core/app-state/DB wiring signals: **189 / 311**
- Handlers without a static wiring signal: **122 / 311** — these are review candidates, not automatic failures, because many are intentionally pure/in-memory, compatibility, health, or facade endpoints.
- Placeholder markers remain: **3**, matching Phase 37.11.

## Mount coverage
All router-bearing modules are accounted for by `main.py`, including:
- the primary API v1 and control-plane routers;
- Timeline and E2E routers mounted with the `/api/v1` prefix;
- optional plugin public/admin routers;
- event API, code intelligence, backend contract, and CP compatibility registrations.

Director is an intentional exception to router-module scanning: it is a mounted FastAPI sub-application created by `services.director.create_app()` and mounted at `/director` in in-process modes, or proxied in separate-service mode.

## Remaining review signals
1. `GET /api/v1/notifications` still returns an empty placeholder collection and has no persistence/service backing.
2. `GET /e2e/director-hands` uses a conservative placeholder heuristic around delegated handlers; this is diagnostic metadata, not an execution bypass.
3. `POST /api/v1/circulation/check` has a fail-closed unavailable/not-implemented branch, while the real monitor path calls `check_circulation()` when available.

## Conclusion
The route surface is now **mount-complete**: no discovered router module is silently orphaned from the application bootstrap. This does not constitute live provider/database/Redis certification; those remain staging-runtime concerns.
