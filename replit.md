# Kanyoza Systems AI Platform v10

## Project overview
An enterprise-grade AI platform built on Python 3.12 + Flask. It handles AI conversations, social media publishing, content generation, code analysis, GitHub automation, and scheduling — capable of scaling from a single Messenger bot to 100,000+ users without architectural changes.

## Architecture (v10)
```
main.py              → thin WSGI entry point
bootstrap.py         → startup orchestration (logging → config → DI → Flask → lifecycle)
application_factory.py → Flask app factory (middleware, error handlers, blueprints)
service_container.py → generic DI container
dependency_registry.py → wires concrete services to interfaces
lifecycle.py         → scheduler start/stop and signal handling

config/
  settings.py        → pydantic-settings strongly-typed config (env vars)
  logging_config.py  → structured JSON logging with correlation IDs
  environments.py    → Environment enum (development / testing / production)

models/
  base.py            → immutable KanyozaModel (Pydantic v2)
  content.py         → TechPost, PostTheme, PostFormat
  conversation.py    → Conversation, ConversationTurn, MemoryProfile, UserProfile
  guardian.py        → RepositoryIssue, GuardianReport
  scheduler.py       → ScheduledJob, JobStatus, JobType
  notification.py    → Notification, NotificationChannel

core/
  exceptions.py      → full exception hierarchy (KanyozaError → specific types)

utils/
  ids.py             → correlation ID (contextvars-based)
  text.py            → sanitize, truncate, split helpers
  datetime_utils.py  → UTC-first datetime helpers
  security.py        → mask_secret, safe path, constant-time compare
```

## Milestone status
- [x] M1 — Foundation & project structure
- [ ] M2 — Configuration, DI, and bootstrap  *(currently wiring)*
- [ ] M3 — Core models and memory
- [ ] M4 — AI abstraction and content engine
- [ ] M5 — Rendering engine (Jinja2 + Playwright → PNG)
- [ ] M6 — Facebook integration and scheduler
- [ ] M7 — Code Guardian
- [ ] M8 — REST API (versioned /api/v1/*)
- [ ] M9 — Testing, documentation, and deployment

## Running the app
```bash
# Development
python main.py

# Production (Gunicorn)
gunicorn main:app
```

## Required environment variables
| Variable | Description |
|---|---|
| `GEMINI_KEY` | Google Gemini API key |
| `PAGE_ACCESS_TOKEN` | Facebook Page access token |
| `PAGE_ID` | Facebook Page ID |
| `VERIFY_TOKEN` | Facebook webhook verify token |

See `ENV.md` for the full list including optional variables.

## Engineering principles
- SOLID, Clean Architecture, Dependency Injection
- Strongly typed (Pydantic v2 models and settings)
- Structured JSON logging with correlation IDs
- Fail fast on missing required config
- Immutable domain models (`frozen=True`)
- No God Objects, no duplicated business logic

## User preferences
- Build milestone-by-milestone (do not generate the entire platform in one shot)
- Keep v9 compatibility shim active until each module is migrated to v10
