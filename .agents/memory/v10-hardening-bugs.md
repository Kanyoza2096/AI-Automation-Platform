---
name: v10 hardening bugs
description: All bugs found and fixed during the production hardening pass with real API keys (Gemini + Facebook live).
---

## Bugs fixed — static analysis pass

| File | Bug | Fix |
|------|-----|-----|
| guardian/auto_fix.py | Called gemini._call_gemini() (nonexistent) | Changed to gemini._call() |
| guardian/auto_fix.py | Branch retry: allowed 422 on retry, could continue to PR | Require HTTP 201 exactly; else return None |
| routes/webhook.py | request.get_json() returns None → AttributeError on .get() | get_json(silent=True) + None guard |
| routes/api.py | 6 POST/PUT routes: get_json() could be None | get_json(silent=True) or {} on all 6 |
| routes/api.py | update_schedule silently dropped out-of-range hours | _validate_hours() raises ValueError → 400 |
| routes/webhook.py | _slash_handler lazy init: race condition | Double-checked locking with threading.Lock() |
| core/memory.py | get_summary() read _summaries without self._lock | Wrapped read in with self._lock |
| analytics/tracker.py | __import__('uuid') inline on every event | Top-level import uuid |
| core/memory.py | __import__('uuid') inline | Top-level import uuid |
| lifecycle.py | Shutdown iterated all 3 attr names per module | Explicit (module, attr) tuple pairs |

## Bugs found during live integration testing (real keys)

| File | Bug | Fix |
|------|-----|-----|
| services/gemini.py | parts[0] assumed to be text; Gemini 2.5 returns thought blocks first | next((p for p in parts if "text" in p), None) |
| services/supabase.py | supabase_service not exported; all api routes had ImportError | Added _SupabaseServiceShim class + module-level supabase_service |
| routes/api.py | 8 sensitive GET routes had no _require_auth() | Added auth guard to dashboard, posts, messages, message-detail, contacts, guardian/results, analytics/performance, tenants |
| routes/owner_commands.py | /status used cfg['page_token_configured'] but key is 'facebook_configured' | Changed to cfg.get('facebook_configured') |
| routes/owner_commands.py | /analytics called self._analytics.stats() (nonexistent) | Changed to get_stats() with correct overview key names |

## Live integration test observations

- Gemini free tier: 20 req/min limit; rate limiter preemptive backoff works
- Webhook HMAC validation: correctly rejects missing/wrong signatures with 403
- Rate limiter: correctly blocks after 10 msgs/user; WARNING logged
- End-to-end: message received → Gemini replied → Facebook send attempted (2.3s total)
- Facebook send for fake PSID: API error 100 (expected for test PSIDs)
- Slash commands /ping /help work; /status was broken (KeyError) — now fixed
- /api/v1/health/deep: confirms live Gemini + Facebook connectivity

**Why:** These were found only with real keys because degraded-mode skips the live code paths.

**How to apply:** When adding new slash commands, always test against to_safe_dict() actual keys. When adding Supabase-using routes, import supabase_service from services.supabase (not raw client functions).
