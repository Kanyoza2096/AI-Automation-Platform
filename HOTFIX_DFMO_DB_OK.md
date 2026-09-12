# Hotfix — DFMO `db_ok` NameError + monitor_checks 400 (2026-08-28)

## From Render logs
```
ERROR | kanyoza.circulation | DFMO patrol error: name 'db_ok' is not defined
POST .../monitor_checks "HTTP/2 400 Bad Request"
```

## Cause
Phase 3 split `db_ok` into `db_health` sensors but left two references to `db_ok` in scoring/blockage logic.

## Fix
- Use `snapshot.database_writable` / `db_health` everywhere
- Richer blockage text (unreachable vs not writable)
- `monitor_checks` insert tries multiple column shapes to avoid hard 400s
- Soft-fix unawaited `get_event_bus()` in AI orchestrator

## Also seen in logs (ops, not code bugs)
- Gemini HTTP 429 rate limit — reduce concurrent LLM calls / upgrade quota
- DeepSeek HTTP 402 Insufficient Balance — top up or set primary to gemini when DS empty
- Socket.io invalid session — free-tier spin-down / reconnect; often benign
