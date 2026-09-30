# Fixes applied 2026-08-27 (Render log diagnosis)

Diagnosed from Omnexa-autonomy free-instance logs:
- Publish denied decision_id=None strict=True
- CustomerVoice missing _is_distracted
- CouncilDecision / dissent AttributeError on older shapes
- monitor_checks HTTP 400
- KanyozaNotifications NameError
- Handler timeout for engine_alert after Reliability success
- TopicSelector empty for brand default

## Files changed

| Path | Change |
|------|--------|
| council/personas/base.py | Added `_is_distracted()` |
| council/personas/customer.py | Pass proposal.id into distraction check |
| council/integration/__init__.py | Harden CouncilDecision (always has .dissent) |
| domain/publishing/canonical_pipeline.py | Council approve → decision_gate.issue; wire decision_id; stop on needs_revision |
| dependency_registry.py | Abort generate_only on Council/decision AttributeError |
| services/email_service.py | Define KanyozaNotifications wrapper |
| core/circulation_monitor.py | Schema-safe DFMO monitor_checks probe |
| plugins/monitor/plugin.py | Better PostgREST 400 logging |
| core/autonomous_loop.py | engine_alert timeout ≥30s; intelligence capped 12s |
| domain/content/topic_selector.py | Platform fallback topics when brand has none |

## Smoke test after redeploy

1. Logs show `Decision issued id=dec_… source=council approved=True` on publish path
2. No `Publish denied … decision_id=None` when council approves
3. No `_is_distracted` AttributeError
4. No `KanyozaNotifications is not defined`
5. DFMO monitor_checks 400s gone (or clear warning with real error body)
6. `Handler timeout for engine_alert` rare after Reliability success

## Still ops/env

- Trend API tokens for non-zero source fetches
- Identify offline Meta engine if alert persists after STALE grace
- Confirm Supabase monitor_checks columns if any 400 remains
