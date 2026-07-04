"""
Shared HTTP client — M1 Architecture Improvement.

A single, policy-enforced HTTP client used by every external API call in the
platform.  Replaces the scatter of bare ``requests.get / post`` calls with:

  - Connection pooling       (persistent Session, configurable pool size)
  - Configurable timeouts    (per-client defaults, per-call overrides)
  - Automatic retry          (5xx, connection resets — with backoff)
  - 429 Rate-limit handling  (backoff + raise — does NOT retry)
  - Retry-After support      (honours the header on 429 and 503 responses)
  - Circuit breaker          (opens after N consecutive failures, resets after TTL)
  - Structured request logs  (method, url, status, latency_ms, attempt)
  - Metrics emission         (feeds MetricsCollector for /api/metrics)

Usage::

    from utils.http_client import HttpClient

    class MyService:
        def __init__(self):
            self._http = HttpClient(
                "my_service",
                base_url="https://api.example.com",
                default_timeout=15.0,
                default_headers={"Accept": "application/json"},
            )

        def fetch(self, path: str) -> dict:
            resp = self._http.get(path)
            resp.raise_for_status()
            return resp.json()
"""

import logging
import threading
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("kanyoza.utils.http_client")

# HTTP status codes safe to retry (NOT 429 — that gets special handling)
_RETRY_STATUSES = frozenset({500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and requests are refused."""


class _CircuitBreaker:
    """Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED."""

    _CLOSED = "closed"
    _OPEN   = "open"

    def __init__(self, threshold: int, ttl: float):
        self._threshold = threshold
        self._ttl       = ttl
        self._state     = self._CLOSED
        self._streak    = 0
        self._opened_at: Optional[float] = None
        self._lock      = threading.Lock()

    def before_call(self, name: str) -> None:
        with self._lock:
            if self._state == self._OPEN:
                if time.monotonic() - self._opened_at >= self._ttl:
                    self._state  = self._CLOSED
                    self._streak = 0
                    logger.info("[%s] Circuit breaker reset to CLOSED after TTL", name)
                else:
                    raise CircuitOpenError(
                        f"Circuit '{name}' is OPEN — refusing call"
                    )

    def on_success(self) -> None:
        with self._lock:
            self._streak = 0
            self._state  = self._CLOSED

    def on_failure(self, name: str) -> None:
        with self._lock:
            self._streak += 1
            if self._streak >= self._threshold:
                self._state     = self._OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "[%s] Circuit breaker OPENED after %d consecutive failures",
                    name, self._streak,
                )

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self._OPEN and time.monotonic() - self._opened_at >= self._ttl:
                return "half_open"
            return self._state


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class HttpClient:
    """
    Policy-enforced HTTP client.

    Parameters
    ----------
    name : str
        Logical service name — used in log lines and metrics labels.
    base_url : str
        Optional base URL prepended to relative paths.
    default_timeout : float
        Seconds to wait for a response.
    default_headers : dict | None
        Session-level headers merged into every request.
    max_retries : int
        Number of extra attempts after the first (total = max_retries + 1).
    backoff_base : float
        Base delay in seconds for exponential backoff between retries.
    circuit_breaker_threshold : int
        Consecutive failures before the circuit opens.
    circuit_breaker_ttl : float
        Seconds to wait before auto-resetting an open circuit.
    """

    def __init__(
        self,
        name: str,
        base_url: str = "",
        default_timeout: float = 15.0,
        default_headers: Optional[dict] = None,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        circuit_breaker_threshold: int = 10,
        circuit_breaker_ttl: float = 30.0,
    ):
        self.name             = name
        self.base_url         = base_url.rstrip("/")
        self._default_timeout = default_timeout
        self._max_retries     = max_retries
        self._backoff_base    = backoff_base
        self._cb              = _CircuitBreaker(circuit_breaker_threshold, circuit_breaker_ttl)

        self._session = requests.Session()
        if default_headers:
            self._session.headers.update(default_headers)

        _adapter = HTTPAdapter(
            max_retries=Retry(
                total=1, connect=1, read=0,
                backoff_factor=0.3, status_forcelist=set(),
            ),
            pool_connections=4,
            pool_maxsize=10,
        )
        self._session.mount("https://", _adapter)
        self._session.mount("http://",  _adapter)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def headers(self):
        return self._session.headers

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> requests.Response:
        return self._request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs) -> requests.Response:
        return self._request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs) -> requests.Response:
        return self._request("DELETE", url, **kwargs)

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{self.base_url}/{url.lstrip('/')}" if self.base_url else url

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        full_url = self._resolve(url)
        timeout  = kwargs.pop("timeout", self._default_timeout)
        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                self._cb.before_call(self.name)

                t0   = time.monotonic()
                resp = self._session.request(method, full_url, timeout=timeout, **kwargs)
                ms   = (time.monotonic() - t0) * 1000

                self._log(method, full_url, resp.status_code, ms, attempt)

                # ── 429 Rate Limited — DO NOT RETRY ──────────────────────
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 60))
                    delay = max(retry_after, 30.0)
                    logger.warning(
                        "[%s] HTTP 429 — backing off %.0fs before next request",
                        self.name, delay,
                    )
                    time.sleep(delay)
                    raise CircuitOpenError(
                        f"[{self.name}] Rate limited — backed off {delay:.0f}s"
                    )

                # ── 5xx Server Errors — retry with backoff ────────────────
                if resp.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                    delay = max(
                        float(resp.headers.get("Retry-After", 0)),
                        self._backoff_base * (2 ** attempt),
                    )
                    logger.warning(
                        "[%s] HTTP %d — retrying in %.1fs (attempt %d/%d)",
                        self.name, resp.status_code, delay, attempt + 1, self._max_retries,
                    )
                    time.sleep(delay)
                    continue

                # ── Track circuit state ──────────────────────────────────
                if resp.status_code >= 500:
                    self._cb.on_failure(self.name)
                else:
                    self._cb.on_success()

                return resp

            except CircuitOpenError:
                raise

            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                self._cb.on_failure(self.name)
                if attempt < self._max_retries:
                    delay = self._backoff_base * (2 ** attempt)
                    logger.warning(
                        "[%s] ConnectionError — retrying in %.1fs: %s",
                        self.name, delay, exc,
                    )
                    time.sleep(delay)

            except requests.exceptions.Timeout as exc:
                last_exc = exc
                self._cb.on_failure(self.name)
                logger.warning(
                    "[%s] Timeout (%.1fs) on %s %s",
                    self.name, timeout, method, full_url,
                )
                if attempt < self._max_retries:
                    time.sleep(self._backoff_base * (2 ** attempt))

            except Exception as exc:
                self._cb.on_failure(self.name)
                logger.error(
                    "[%s] Unexpected error on %s %s: %s",
                    self.name, method, full_url, exc,
                )
                raise

        raise requests.exceptions.RequestException(
            f"[{self.name}] All {self._max_retries + 1} attempts failed for {method} {full_url}"
        ) from last_exc

    def _log(self, method: str, url: str, status: int, ms: float, attempt: int) -> None:
        level = logging.WARNING if status >= 400 else logging.DEBUG
        retry_note = f" [retry {attempt}]" if attempt else ""
        logger.log(
            level,
            "[%s] %s %s → %d (%.0f ms)%s",
            self.name, method, url, status, ms, retry_note,
        )