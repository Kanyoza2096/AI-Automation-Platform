"""
Kanyoza Systems AI Platform v10 — Entry Point.

This file contains almost no logic. All startup work happens in bootstrap.py.
Gunicorn / Flask dev server imports `app` from this module.

Run locally:
    python main.py

Run with Gunicorn + eventlet (required for WebSocket support):
    gunicorn main:app --worker-class eventlet --workers 1

IMPORTANT: eventlet.monkey_patch() MUST be the very first thing that runs so
that the stdlib networking layer is patched before any other import.
"""

# ── Eventlet monkey-patch — must precede every other import ─────────────────
import eventlet
eventlet.monkey_patch()
# ─────────────────────────────────────────────────────────────────────────────

from bootstrap import bootstrap

# Build and expose the WSGI application object
app = bootstrap()

if __name__ == "__main__":
    from config.settings import settings
    app.run(
        host="0.0.0.0",
        port=settings.port,
        debug=settings.env.is_development,
        threaded=True,
        use_reloader=False,  # Reloader breaks signal handlers
    )
