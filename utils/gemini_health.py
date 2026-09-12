# kanyoza/utils/gemini_health.py
import threading
import time
import logging

logger = logging.getLogger(__name__)

# ── Shared cache ────────────────────────────────────────────────────────────────
_cache = {
    "status": "unknown",      # "ok" | "degraded" | "error" | "unknown"
    "latency_ms": None,
    "last_checked": None,
    "error": None,
}
_lock = threading.Lock()

CHECK_INTERVAL_SECONDS = 300  # probe Gemini every 5 minutes, not on every request


def get_cached_status() -> dict:
    """Return the last known Gemini health — never calls the API."""
    with _lock:
        return dict(_cache)


def _probe_once(client, model: str = "gemini-2.5-flash"):
    """Lightweight probe: list models instead of generateContent."""
    import time as _time
    t0 = _time.monotonic()
    try:
        # models.list() costs zero quota — it's a metadata call only
        list(client.models.list())
        latency = round((_time.monotonic() - t0) * 1000)
        with _lock:
            _cache.update(status="ok", latency_ms=latency,
                          last_checked=_time.time(), error=None)
    except Exception as exc:
        latency = round((_time.monotonic() - t0) * 1000)
        with _lock:
            _cache.update(status="error", latency_ms=latency,
                          last_checked=_time.time(), error=str(exc))
        logger.warning("[gemini-health] probe failed: %s", exc)


def start_background_checker(client, model: str = "gemini-2.5-flash"):
    """
    Call once at app startup (e.g. in create_app()).
    Runs an initial probe then repeats every CHECK_INTERVAL_SECONDS.
    Never blocks a request.
    """
    def _loop():
        while True:
            _probe_once(client, model)
            time.sleep(CHECK_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, daemon=True, name="gemini-health-checker")
    t.start()
    logger.info("[gemini-health] background checker started (interval=%ds)", CHECK_INTERVAL_SECONDS)
