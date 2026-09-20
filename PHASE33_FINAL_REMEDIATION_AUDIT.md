# KDO Phase 33 — Final Remediation Pass

## Purpose
This pass validates and remediates the additional audit findings supplied after the previous certification attempt. The supplied full-mode boot report remains a hard release gate.

## Remediated
- Durable publish finalization now preserves the worker lease owner until the durable completion/quarantine RPC succeeds; the lease is cleared only afterward.
- Added canonical module-level `core.publish_outbox.enqueue()` delegating to the configured singleton used by lifecycle wiring.
- Mounted the previously omitted control-plane routers in `main.py`: metabolism, organism body, outbox, reports, SLO, web intelligence, unified health, council, circulation, and pulse.
- Added a hard maximum of 12 web-agent steps and validated malformed `max_steps` input.
- Dead-letter JSON persistence now serializes non-JSON-native values with `default=str`.
- TaskQueue shutdown no longer silently drops sentinels because of a full queue; submission checks running state under the same lock as shutdown.

## Confirmed already fixed in current snapshot
- Species-authenticated request context uses a defined Supabase client; the previously reported `supabase_client` NameError is absent.

## Still requires further remediation/staging proof
- Scheduled posts in `processing` need stale-claim recovery and durable lease semantics.
- Dead-letter retry should use durable claim/ack semantics rather than destructive pop-before-execution.
- Redis render dequeue requires durable acknowledgement/lease recovery for crash safety.
- Render storage failure must be represented as a failed render, never a successful result with an empty URL.
- Legacy migrations granting `anon` access and disabling RLS must be corrected through a forward migration and verified against the deployed database.
- Alternate monitor implementation must use centralized SSRF validation or be removed from the importable runtime surface.
- Monitor admin operations require workspace/resource scoping.
- SSRF protection needs DNS pinning/connection-time enforcement to close rebinding TOCTOU.
- Webhook failure responses need truthful non-2xx semantics where provider retry is required.
- Duplicate OpenAPI operation IDs need deterministic unique IDs.
- The duplicate FastAPI factory should be removed/consolidated.
- Startup-created app state needs safe dependency guards for pre-lifespan/direct ASGI use.
- Full-mode Gunicorn/Uvicorn live HTTP verification remains mandatory.

## Verification
- `python -m compileall -q .` — PASS
- `pytest -q` — 87 passed, 0 failed
- Direct application import/OpenAPI probe was blocked in this environment by missing installed dependency `socketio`; this is an environment/package installation issue and must be covered by deployment dependency verification.

## Release status
NOT PRODUCTION CERTIFIED. The code is substantially hardened, but the remaining queue, security, migration, webhook, and live full-mode runtime gates must be resolved and verified before production.
