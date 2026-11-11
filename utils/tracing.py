"""
Request tracing — captures timing data for every API step
and publishes to Redis for the Digital Twin frontend.
"""

import time
import json
import functools
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger("kanyoza.tracing")

# In-memory fallback if Redis is down
_traces_buffer: list = []
MAX_BUFFER_SIZE = 200


def _publish_trace(trace: dict) -> None:
    """Publish a trace to Redis, with in-memory fallback."""
    try:
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if container:
            from core.redis_client import UpstashRedisClient
            redis = container.try_resolve(UpstashRedisClient)
            if redis and redis.is_available:
                # Push to Redis list
                redis.lpush("kanyoza:traces", json.dumps(trace, default=str))
                # Trim to last 500 traces
                # (Upstash REST doesn't support LTRIM, so we store with TTL)
                redis.set(f"trace:{trace['id']}", json.dumps(trace, default=str), ex=3600)
                return
    except Exception:
        pass
    
    # In-memory fallback
    global _traces_buffer
    _traces_buffer.insert(0, trace)
    _traces_buffer = _traces_buffer[:MAX_BUFFER_SIZE]


def trace(step_name: str, metadata: Optional[dict] = None):
    """
    Decorator that times a function and publishes the trace.
    
    Usage:
        @trace("supabase_insert", metadata={"table": "church_members"})
        def insert_member(data):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            error = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)[:200]
                raise
            finally:
                duration_ms = round((time.monotonic() - start) * 1000, 2)
                
                # Get correlation ID from Flask request context
                correlation_id = ""
                try:
                    from flask import request, g
                    correlation_id = g.get("correlation_id", "")
                except Exception:
                    pass
                
                trace_data = {
                    "id": f"trace_{int(time.time() * 1000)}_{hash(step_name) % 10000}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "correlation_id": correlation_id,
                    "step": step_name,
                    "duration_ms": duration_ms,
                    "metadata": metadata or {},
                    "error": error,
                }
                
                _publish_trace(trace_data)
        
        return wrapper
    return decorator


def get_recent_traces(limit: int = 50) -> list:
    """Get recent traces from Redis or in-memory buffer."""
    try:
        from flask import current_app
        container = current_app.extensions.get("service_container")
        if container:
            from core.redis_client import UpstashRedisClient
            redis = container.try_resolve(UpstashRedisClient)
            if redis and redis.is_available:
                traces = []
                # Read from Redis list (simulated by reading recent trace keys)
                # In production, you'd use LRANGE; Upstash REST uses GET on known keys
                for i in range(min(limit, 50)):
                    trace_json = redis.get(f"trace:recent:{i}")
                    if trace_json:
                        try:
                            traces.append(json.loads(trace_json))
                        except Exception:
                            pass
                return traces
    except Exception:
        pass
    
    return _traces_buffer[:limit]
