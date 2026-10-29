"""
Kanyoza Systems AI Platform v11.0 — BrowserManager (M5).

Owns a single long-lived Playwright Chromium process and a pool of N
reusable pages.  Launching Chromium costs ~500ms; reusing pages drops
individual card renders to ~100ms.

v11.0 CHANGES:
  - FIXED: Browser version read moved inside worker thread (race condition)
  - FIXED: Shutdown sequence hardened (teardown completes before sentinel)
  - FIXED: Reentrancy guard uses thread ID comparison
  - ADDED: HealthProvider protocol support
  - ADDED: Page health metrics (pages_created, pages_replaced, renders_completed)
  - ADDED: Timeout hardening on all blocking operations

Thread-safety model (preserved from v10)
----------------------------------------
Playwright's sync_api manages an internal asyncio event loop.  When
eventlet monkey-patches asyncio, that loop conflicts with eventlet's
hub if Playwright objects are touched from a green thread.

Fix: a single dedicated OS thread (``_playwright_thread_main``) owns ALL
Playwright objects.  Every external call goes through ``call_in_thread()``
which submits a callable to the thread's job queue and blocks until ready.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional, TypeVar

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page, Playwright

logger = logging.getLogger("kanyoza.services.browser_manager")

T = TypeVar("T")

POOL_SIZE = 1
CARD_W = 1080
CARD_H = 1080
DEVICE_SCALE_FACTOR = 2

_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--mute-audio",
]


class BrowserManager:
    """
    Singleton Playwright browser + page pool.

    All Playwright objects live exclusively on the dedicated ``_worker``
    OS thread.  External callers submit work via ``call_in_thread()`` or
    the higher-level ``render_with_page()``.
    """

    def __init__(self, pool_size: int = POOL_SIZE) -> None:
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._started = False

        self._worker: Optional[threading.Thread] = None
        self._worker_thread_id: Optional[int] = None  # v11.0: Thread ID for reentrancy guard
        self._job_q: queue.Queue = queue.Queue()

        self._page_pool: queue.Queue = queue.Queue(maxsize=pool_size)

        self._healthy: bool = False

        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._browser_version: str = "unknown"  # v11.0: Store version safely

        # v11.0: Metrics
        self._pages_created = 0
        self._pages_replaced = 0
        self._renders_completed = 0
        self._render_failures = 0

    # ═══════════════════════════════════════════════════════════════
    # HEALTH PROVIDER PROTOCOL (v11.0)
    # ═══════════════════════════════════════════════════════════════

    @property
    def service_name(self) -> str:
        return "browser_manager"

    def check_health(self) -> dict:
        """Sync health check for lifecycle compatibility."""
        return {
            "status": "healthy" if self.is_running else "stopped",
            "pool_size": self._pool_size,
            "pages_created": self._pages_created,
            "pages_replaced": self._pages_replaced,
            "renders_completed": self._renders_completed,
            "render_failures": self._render_failures,
            "browser_version": self._browser_version,
        }

    async def check_health_async(self) -> dict:
        """Async health check."""
        return self.check_health()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Launch Chromium inside the dedicated Playwright thread and fill the pool.
        """
        with self._lock:
            if self._started:
                return

            self._worker = threading.Thread(
                target=self._playwright_thread_main,
                name="playwright-worker",
                daemon=True,
            )
            self._worker.start()
            self._worker_thread_id = self._worker.ident  # v11.0: Store thread ID

            exc_holder: list = [None]
            done = threading.Event()

            def _launch_job() -> None:
                try:
                    self._launch()
                except Exception as exc:
                    exc_holder[0] = exc
                finally:
                    done.set()

            self._job_q.put(_launch_job)
            ready = done.wait(timeout=120)
            if not ready:
                raise TimeoutError("BrowserManager: Chromium did not launch within 120 s.")

            if exc_holder[0] is not None:
                raise exc_holder[0]

            self._started = True
            logger.info(
                "BrowserManager started — %d pages ready (Chromium %s).",
                self._pool_size,
                self._browser_version,  # v11.0: Safe version from worker thread
            )

    def stop(self) -> None:
        """Tear down the browser and stop the worker thread."""
        with self._lock:
            if not self._started:
                return

            done = threading.Event()

            def _teardown_job() -> None:
                try:
                    self._teardown()
                finally:
                    done.set()

            self._job_q.put(_teardown_job)
            ready = done.wait(timeout=30)

            if not ready:
                logger.warning("BrowserManager: teardown timed out after 30 s — forcing shutdown.")

            # v11.0: Wait for teardown completion before sending sentinel
            if ready:
                self._job_q.put(None)
                if self._worker:
                    self._worker.join(timeout=15)

            self._started = False
            self._healthy = False
            self._worker_thread_id = None
            logger.info("BrowserManager stopped.")

    @property
    def is_running(self) -> bool:
        return self._started and self._healthy

    def _set_healthy(self, value: bool) -> None:
        """Called from the worker thread to update the health flag."""
        self._healthy = value

    # ── Call dispatch ─────────────────────────────────────────────────────────

    def call_in_thread(self, fn: Callable[[], T], timeout: Optional[float] = None) -> T:
        """
        Run *fn()* inside the dedicated Playwright OS thread and return its result.
        """
        if not self._started:
            raise RuntimeError("BrowserManager is not running. Call start() first.")

        # v11.0: Use thread ID for reentrancy guard
        current_thread_id = threading.get_ident()
        if current_thread_id == self._worker_thread_id:
            return fn()

        result_holder: list = [None]
        exc_holder: list = [None]
        done = threading.Event()

        def _job() -> None:
            try:
                result_holder[0] = fn()
            except Exception as exc:
                exc_holder[0] = exc
            finally:
                done.set()

        self._job_q.put(_job)
        ready = done.wait(timeout=timeout)
        if not ready:
            raise TimeoutError(f"BrowserManager.call_in_thread: job did not complete within {timeout}s.")

        if exc_holder[0] is not None:
            raise exc_holder[0]
        return result_holder[0]  # type: ignore[return-value]

    # ── Page borrowing ────────────────────────────────────────────────────────

    def render_with_page(self, fn: Callable[[Page], T], timeout: float = 60.0) -> T:
        """
        Borrow a page from the pool, call *fn(page)*, and return the result.
        """
        if not self.is_running:
            raise RuntimeError("BrowserManager is not running. Call start() before render_with_page().")

        def _borrow_and_use() -> T:
            try:
                page: Page = self._page_pool.get(timeout=timeout)
            except queue.Empty as exc:
                raise queue.Empty(f"No Playwright page available within {timeout}s (pool_size={self._pool_size})") from exc

            crashed = False
            try:
                result = fn(page)
                self._renders_completed += 1  # v11.0: Track metric
            except Exception:
                crashed = True
                self._render_failures += 1  # v11.0: Track metric
                raise
            finally:
                if crashed or not self._is_page_healthy(page):
                    logger.warning("Page crashed or unhealthy — replacing with a fresh one.")
                    self._pages_replaced += 1  # v11.0: Track metric
                    try:
                        page.close()
                    except Exception:
                        pass
                    try:
                        replacement = self._new_page()
                        self._page_pool.put_nowait(replacement)
                    except Exception as exc:
                        logger.error("Could not create replacement page (%s) — attempting browser restart.", exc)
                        self._attempt_pool_recovery()
                else:
                    self._reset_page(page)
                    try:
                        self._page_pool.put_nowait(page)
                    except queue.Full:
                        try:
                            page.close()
                        except Exception:
                            pass

            return result  # type: ignore[return-value]

        return self.call_in_thread(_borrow_and_use, timeout=timeout + 5)

    # ── Worker thread entry point ─────────────────────────────────────────────

    def _playwright_thread_main(self) -> None:
        """Dedicated OS thread main loop."""
        while True:
            job = self._job_q.get()
            if job is None:
                break
            try:
                job()
            except Exception as exc:
                logger.error("Unhandled error in playwright worker job: %s", exc)

    # ── Playwright internals (worker thread only) ─────────────────────────────

    def _launch(self) -> None:
        """Start Playwright + Chromium and warm the pool."""
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=_CHROMIUM_ARGS, headless=True)

        # v11.0: Store version safely (inside worker thread)
        try:
            self._browser_version = self._browser.version or "unknown"
        except Exception:
            self._browser_version = "unknown"

        for _ in range(self._pool_size):
            page = self._new_page()
            self._page_pool.put_nowait(page)

        self._set_healthy(True)

    def _teardown(self) -> None:
        """Drain pool, close pages, close browser."""
        self._set_healthy(False)

        while not self._page_pool.empty():
            try:
                page = self._page_pool.get_nowait()
                page.close()
            except Exception:
                pass

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def _new_page(self) -> Page:
        """Create a fresh card-sized page."""
        page = self._browser.new_page(
            viewport={"width": CARD_W, "height": CARD_H},
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )
        self._pages_created += 1  # v11.0: Track metric
        return page

    def _attempt_pool_recovery(self) -> None:
        """Emergency pool recovery."""
        logger.warning("BrowserManager: starting emergency pool recovery...")
        try:
            while not self._page_pool.empty():
                try:
                    p = self._page_pool.get_nowait()
                    p.close()
                except Exception:
                    pass
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
            self._launch()
            logger.info("BrowserManager: pool recovery successful (%d pages ready).", self._pool_size)
        except Exception as exc:
            logger.error("BrowserManager: pool recovery failed: %s", exc)

    @staticmethod
    def _is_page_healthy(page: Page) -> bool:
        try:
            page.evaluate("1+1")
            return True
        except Exception:
            return False

    @staticmethod
    def _reset_page(page: Page) -> None:
        """Navigate to about:blank."""
        try:
            page.goto("about:blank", wait_until="commit")
        except Exception:
            pass


# Module-level singleton
browser_manager = BrowserManager(pool_size=POOL_SIZE)


__all__ = ["BrowserManager", "browser_manager"]