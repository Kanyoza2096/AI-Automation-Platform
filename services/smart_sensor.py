"""Reusable smart-sensor control primitives.

Sensors should distinguish disabled, idle, failed and productive states, avoid
repeated attempts against broken sources, and adapt their polling cadence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class CircuitBreaker:
    threshold: int = 5
    cooldown_seconds: int = 3600
    failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def can_attempt(self) -> bool:
        return self.state in {"closed", "half_open"}

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def status(self) -> Dict[str, Any]:
        state = self.state
        cooldown_until = None
        if self.opened_at is not None:
            cooldown_until = self.opened_at + self.cooldown_seconds
        return {"state": state, "failures": self.failures, "cooldown_until_monotonic": cooldown_until}


@dataclass
class AdaptiveFrequency:
    base_interval: int = 300
    min_interval: int = 60
    max_interval: int = 3600
    current_interval: int = field(init=False)

    def __post_init__(self) -> None:
        self.current_interval = self.base_interval

    def on_result(self, got_data: bool) -> None:
        if got_data:
            self.current_interval = max(self.min_interval, self.current_interval // 2)
        else:
            self.current_interval = min(self.max_interval, self.current_interval * 2)

    def status(self) -> Dict[str, int]:
        return {
            "base_interval_seconds": self.base_interval,
            "min_interval_seconds": self.min_interval,
            "max_interval_seconds": self.max_interval,
            "current_interval_seconds": self.current_interval,
        }
