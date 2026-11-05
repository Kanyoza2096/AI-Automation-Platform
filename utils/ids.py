"""
Correlation ID utilities.

Every request and background job is tagged with a UUID4 correlation ID.
The ID is stored in a contextvars.ContextVar so it propagates automatically
through the call stack without being threaded through every function signature.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

# Module-level context variable — holds the current request / job correlation ID
correlation_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "correlation_id", default=None
)

# Stable for this Python process; shared by in-process and cross-process
# transports so a process never consumes its own persisted event copy.
_PROCESS_INSTANCE_ID = f"proc_{uuid.uuid4().hex}"


def get_process_instance_id() -> str:
    """Return the stable identity shared by all KDO transports in this process."""
    return _PROCESS_INSTANCE_ID


def generate_correlation_id() -> str:
    """Generate a new UUID4 correlation ID string."""
    return str(uuid.uuid4())


def get_current_correlation_id() -> Optional[str]:
    """
    Return the correlation ID bound to the current execution context.
    Returns None when called outside a request or job context.
    """
    return correlation_id_ctx.get()


def set_correlation_id(cid: Optional[str] = None) -> str:
    """
    Set a correlation ID on the current context.
    Generates a new one if *cid* is not provided.
    Returns the ID that was set.
    """
    cid = cid or generate_correlation_id()
    correlation_id_ctx.set(cid)
    return cid
