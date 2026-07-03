---
name: WebSocket dashboard implementation
description: How the real-time dashboard WebSocket server and stats API are wired together
---

## Eventlet must be monkey-patched before all imports
`eventlet.monkey_patch()` is the very first statement in `main.py`, before `from bootstrap import bootstrap`. Missing or late monkey-patching causes stdlib networking to not be patched, breaking async I/O under the eventlet worker.

**Why:** Gunicorn uses `--worker-class eventlet`; without the patch, regular threading and sockets are not cooperative, causing hangs and broken WebSocket upgrades.

## DI EventBus vs module singleton
`lifecycle._wire_socket_events(container)` resolves the bus via `container.resolve(EventBus)`. This is the same bus instance used by workflows, EventRecorder, and all services. `get_event_bus()` returns a **different** module-level singleton that receives no events from DI-driven code.

**Why:** If you subscribe on `get_event_bus()`, real-time events from the posting workflow never reach SocketIO.

**How to apply:** Any new lifecycle subscriber that needs to react to domain events must also use `container.resolve(EventBus)`, not `get_event_bus()`.

## SocketIO init order
`_register_socketio(app)` is called inside `create_app()` (factory), which runs before `register_lifecycle()`. SocketIO is fully init_app'd before lifecycle wiring, so `on_connect` can call `build_live_stats()` safely at connection time.

## Service health probes in build_live_stats()
- Facebook: `fb_svc.is_configured` (property, checks `page_token` and `page_id`). NOT `_page_access_token` (doesn't exist).
- Converter: return `ok: True` always — don't call `health_check()` in /live because it makes a live HTTP fetch. `_cache` dict holds per-pair cached rates.
- Gemini / Supabase: check settings flags only, no network call.

## Namespace
All browser clients connect to `/dashboard` namespace. On connect they receive an immediate `stats` event. They can also send `request_stats` any time for a fresh snapshot.
