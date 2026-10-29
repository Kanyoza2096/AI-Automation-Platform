from fastapi.responses import JSONResponse
import logging
import asyncio
import html as _html_mod
import re
from typing import Any, Callable

from api.dependencies import correlation_id_var

HTML_TAG_RE = re.compile(r"<[^>]+>")

logger = logging.getLogger('kanyoza.api.helpers')

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS — Utility Functions
# ═══════════════════════════════════════════════════════════════════════════


def _sanitize(value: str) -> str:
    """
    A03: Strip HTML tags, normalize whitespace, escape HTML entities.
    Defense-in-depth: applied at Pydantic validators AND service layer.
    """
    if not value:
        return ""
    clean = HTML_TAG_RE.sub("", str(value))
    clean = " ".join(clean.split())
    return _html_mod.escape(clean, quote=False)


def _ok(data: dict | None = None, status_code: int = 200) -> JSONResponse:
    """Standardized success response — consistent with existing frontend contract."""
    body: dict = {"ok": True}
    if data:
        body.update(data)
    return JSONResponse(content=body, status_code=status_code)


def _err(msg: str, status_code: int = 400) -> JSONResponse:
    """Standardized error response."""
    return JSONResponse(
        content={"ok": False, "error": msg}, status_code=status_code
    )


def _safe_err(exc: Exception, endpoint: str, status_code: int = 500) -> JSONResponse:
    """
    A05/A09: Logs full error internally, returns sanitized message externally.
    In production, hides stack traces and internal details.
    """
    cid = correlation_id_var.get()
    logger.error("[%s] %s error: %s", cid, endpoint, exc, exc_info=True)
    try:
        from config.settings import settings
        if settings.is_development:
            return _err(str(exc), status_code)
    except Exception:
        pass
    return _err("An internal error occurred. Please try again later.", status_code)


async def _async_wrap(func: Callable, *args, **kwargs) -> Any:
    """
    Wraps synchronous function calls in run_in_executor.
    Preserves all existing sync services/repos without modification.
    Uses the default thread pool executor (asyncio's internal pool).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC SURFACE
# ═══════════════════════════════════════════════════════════════════════════
#
# Explicit __all__ so that `from api.v1_helpers import *` in downstream router
# modules (guardian.py, workflows.py, metrics.py, analytics.py, etc.) exports
# the underscore-prefixed helpers. Python's default wildcard behaviour skips
# names starting with "_", which silently dropped _ok / _err / _safe_err /
# _async_wrap from every router that relied on the wildcard. Declaring __all__
# overrides that convention and includes them.

__all__ = [
    "HTML_TAG_RE",
    "_sanitize",
    "_ok",
    "_err",
    "_safe_err",
    "_async_wrap",
]
