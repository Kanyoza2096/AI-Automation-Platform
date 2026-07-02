---
name: v10 architecture decisions
description: Key design choices made in Milestone 1 that all later milestones must be consistent with
---

## Settings
- Use `pydantic-settings` `BaseSettings` — env vars loaded automatically
- `ClassVar` required for non-field class attributes (Pydantic v2 strict)
- `validate_required()` exits in production, warns+continues in development
- `SESSION_SECRET` must be a Replit Secret — app refuses to start in production without it

**Why:** Fail-fast in prod, testable in dev. Same pattern should apply to every new required secret added in later milestones.

## Models
- All domain models inherit `KanyozaModel(BaseModel, frozen=True)`
- Use `.model_copy(update={...})` for derived instances — never mutate
- `from_*` classmethods for parsing external payloads (e.g. `TechPost.from_gemini_response`)

## Logging
- `config/logging_config.py` → `get_logger(__name__)` everywhere
- Correlation ID stored in `utils/ids.correlation_id_ctx` (contextvars)
- Set via `set_correlation_id()` in `application_factory._register_request_hooks`
- Background threads must call `set_correlation_id(cid)` explicitly — contextvars do NOT propagate across threads automatically

## Service Container
- `service_container.py` → `ServiceContainer` with thread-safe double-checked locking
- `dependency_registry.py` → only place concrete classes are wired
- Each milestone adds its services to `_register_services()` in `dependency_registry.py`

## v9 Compatibility
- `application_factory._register_v9_routes()` registers old Flask routes
- `lifecycle._start_v9_post_scheduler()` starts old scheduler
- `utils/security.py` exports `verify_facebook_signature`, `sanitize_message`, `detect_abuse` for v9 webhook routes
- Remove shims one module at a time as v10 equivalents are built

## hmac usage
- `verify_facebook_signature` uses `hmac.new(secret, payload, sha256)` — note it is `hmac.new`, not `hmac.HMAC` directly
