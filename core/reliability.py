"""
Reliability and Resilience Patterns.
Provides CircuitBreaker, programatic RetryManager, file-based DeadLetterQueue (DLQ),
and simple HealthChecker service.
"""

from __future__ import annotations

import json
import os
import time
import logging
import random
from typing import Callable, Any, Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger("kanyoza.core.reliability")

# ── Circuit Breaker ───────────────────────────────────────────────────

class CircuitBreakerOpenException(Exception):
    """Raised when trying to execute an action while the circuit is open."""
    pass


class CircuitBreaker:
    """
    State machine that wraps calls to external services.
    States: CLOSED, OPEN, HALF-OPEN
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_state_change = time.time()

    def __call__(self, func: Callable, *args, **kwargs) -> Any:
        self._check_state()
        
        if self.state == "OPEN":
            raise CircuitBreakerOpenException(f"Circuit breaker '{self.name}' is OPEN.")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise exc

    def _check_state(self):
        if self.state == "OPEN":
            # If recovery timeout elapsed, transition to HALF-OPEN
            if time.time() - self.last_state_change > self.recovery_timeout:
                logger.warning("Circuit breaker '%s' entering HALF-OPEN state.", self.name)
                self.state = "HALF-OPEN"
                self.last_state_change = time.time()

    def _on_success(self):
        if self.state == "HALF-OPEN":
            logger.info("Circuit breaker '%s' recovered to CLOSED state.", self.name)
            self.state = "CLOSED"
            self.failure_count = 0
            self.last_state_change = time.time()
        elif self.state == "CLOSED":
            # Clear count on clean success
            self.failure_count = 0

    def _on_failure(self, exc: Exception):
        self.failure_count += 1
        logger.warning(
            "Circuit breaker '%s' failure %d/%d: %s",
            self.name, self.failure_count, self.failure_threshold, exc
        )
        
        if self.state == "HALF-OPEN" or self.failure_count >= self.failure_threshold:
            logger.error("Circuit breaker '%s' tripped to OPEN state.", self.name)
            self.state = "OPEN"
            self.last_state_change = time.time()


# ── programmatic Retry Manager ───────────────────────────────────────

class RetryManager:
    """Programmatic helper to run functions with exponential backoff."""
    
    @staticmethod
    def run(
        func: Callable,
        max_retries: int = 3,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
        exceptions: tuple = (Exception,),
        *args,
        **kwargs
    ) -> Any:
        last_exc = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except exceptions as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    delay = base_delay * (backoff_factor ** attempt)
                    delay += random.uniform(0, delay * 0.3)
                    logger.warning(
                        "RetryManager: Attempt %d/%d failed for %s. Retrying in %.2fs. Error: %s",
                        attempt + 1, max_retries, func.__name__, delay, exc
                    )
                    time.sleep(delay)
        raise last_exc


# ── Dead Letter Queue (DLQ) ───────────────────────────────────────────

@dataclass
class DLQJob:
    job_id: str
    workspace_id: str
    brand_id: str
    content: str
    image_url: Optional[str] = None
    error_reason: str = ""
    timestamp: float = 0.0


class DeadLetterQueue:
    """Local file-based DLQ to persist failed publish tasks for manual replay."""

    def __init__(self, file_path: str = "dlq.json"):
        self.file_path = file_path

    def enqueue(self, workspace_id: str, brand_id: str, content: str, image_url: Optional[str], error: str) -> str:
        job_id = f"dlq-{uuid_hex()}"
        job = DLQJob(
            job_id=job_id,
            workspace_id=workspace_id,
            brand_id=brand_id,
            content=content,
            image_url=image_url,
            error_reason=error,
            timestamp=time.time()
        )
        
        jobs = self.list_jobs()
        jobs.append(asdict(job))
        
        with open(self.file_path, "w") as f:
            json.dump(jobs, f, indent=2)
            
        logger.warning("Job '%s' added to Dead Letter Queue (DLQ): %s", job_id, error)
        return job_id

    def list_jobs(self) -> List[Dict]:
        if not os.path.exists(self.file_path):
            return []
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def remove_job(self, job_id: str) -> bool:
        jobs = self.list_jobs()
        new_jobs = [j for j in jobs if j["job_id"] != job_id]
        if len(new_jobs) == len(jobs):
            return False
            
        with open(self.file_path, "w") as f:
            json.dump(new_jobs, f, indent=2)
        return True


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ── Health Checker ────────────────────────────────────────────────────

class HealthChecker:
    """Performs health check validation across platform connectors."""
    
    def __init__(self, registry: Any):
        self.registry = registry

    def check_all(self) -> Dict[str, Dict]:
        results = {}
        for name in self.registry.names:
            plugin = self.registry.get(name)
            if plugin:
                try:
                    results[name] = plugin.health()
                except Exception as exc:
                    results[name] = {"ok": False, "error": str(exc)}
        return results
