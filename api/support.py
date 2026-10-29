"""
Shared API support symbols — extracted for modular routers (architecture §26).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Optional

from fastapi.responses import JSONResponse

logger = logging.getLogger("kanyoza.api.support")

auth_workspace_id_var: ContextVar[Optional[str]] = ContextVar("auth_workspace_id", default=None)
MAX_PAGE_SIZE = 100


def ok(data: dict | None = None, status_code: int = 200) -> JSONResponse:
    payload = {"ok": True, **(data or {})}
    return JSONResponse(payload, status_code=status_code)


def err(msg: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status_code)


def safe_err(exc: Exception, endpoint: str, status_code: int = 500) -> JSONResponse:
    logger.exception("%s failed: %s", endpoint, exc)
    return err("Internal server error", status_code)
