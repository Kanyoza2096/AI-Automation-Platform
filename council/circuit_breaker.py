"""Circuit breaker for Council availability."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger("kanyoza.council.circuit")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CouncilCircuitBreaker:
    """Protects the platform from Council failures."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()
    
    @property
    def is_open(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return False
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                return False
            return True
        return False
    
    async def record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
    
    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning("Council circuit breaker OPEN")
    
    @property
    def state(self) -> str:
        return self._state.value