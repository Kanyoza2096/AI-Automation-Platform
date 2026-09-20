# Kanyoza Systems AI Platform — Master Context

## What We Built
Enterprise AI automation platform (Python 3.14 + Flask + Supabase + Render).

## Architecture (v10)
- Clean Architecture with DI (service_container.py, dependency_registry.py)
- Pydantic v2 models (models/*.py)
- EventBus + EventRecorder + SocketIO real-time dashboard
- Plugin system (Facebook, Twitter, LinkedIn)
- Workflow engine (PostPublishWorkflow)
- BrowserManager + Playwright → 1080x1080 PNG cards
- REST API /api/v1/* + WebSocket /socket.io

## Key Files
- application_factory.py — Flask app with CORS, error handlers, routes
- bootstrap.py — Startup orchestration
- lifecycle.py — Scheduler, plugins, graceful shutdown
- config/settings.py — Pydantic-settings (all env vars typed)
- core/socket_server.py — SocketIO (async_mode="threading")
- scheduler/post_scheduler.py — 10 posts/day, slot dedup
- services/content_engine.py — Single Gemini call → TechPost → Card
- services/card_renderer.py — SVG → Jinja2 → Playwright → PNG
- products/converter/routes.py — MWK currency converter API

## Critical Decisions
- DO NOT use eventlet — crashes on Python 3.14
- SocketIO uses async_mode="threading" with polling+websocket transports
- Gemini health check removed from /api/v1/health/deep (was causing 429)
- Guardian disabled due to Python 3.14 + gitpython bug
- BrowserManager pool reduced to 1 page (free tier RAM)

## Render Config
- Start: gunicorn main:app --workers 1 --threads 4 --timeout 300
- Env vars: GEMINI_KEY, PAGE_ACCESS_TOKEN, PAGE_ID, VERIFY_TOKEN, APP_SECRET,
  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
  HF_TOKEN, HCTI_USER_ID, HCTI_API_KEY, MASTER_API_TOKEN,
  TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_REFRESH_TOKEN,
  GUARDIAN_ENABLED=false, SESSION_SECRET, GITHUB_USERNAME, GITHUB_TOKEN

## Current Issues To Fix
1. Gemini 401 — API key may need rotation
2. analytics_events missing 'topic' column — ALTER TABLE needed
3. Twitter plugin smart truncation for 280 chars

## Repos
- Backend: https://github.com/Kanyoza2096/AI-Automation-Platform
- Dashboard: React + Vite + Tailwind on Cloudflare Pages
- Converter: mwk-converter.pages.dev

## URLs
- Bot: https://kanyoza-systems-bot.onrender.com
- Health: /health, /api/v1/health/deep
- API: /api/v1/currencies, /api/v1/convert, /api/v1/dashboard/live
