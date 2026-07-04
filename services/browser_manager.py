"""
Kanyoza Systems AI Platform v10 — BrowserManager (M5).

Owns a single long-lived Playwright Chromium process and a pool of N
reusable pages.  Launching Chromium costs ~500ms; reusing pages drops
individual card renders to ~100ms.

Thread-safety model
-------------------
Playwright's sync_api manages an internal asyncio event loop.  When
eventlet monkey-patches asyncio, that loop conflicts with eventlet's
hub if Playwright objects are touched from a green thread, producing:

    RuntimeError: loop <...> is not the running loop

Fix: a single dedicated OS thread (``_playwright_thread_main``) owns ALL
Playwright objects.  Every external call goes through ``call_in_thread()``
which submits a callable to the thread's job queue and blocks (green-yield
friendly via ``threading.Event``) until the result is ready.

Usage (via lifecycle.py):
    browser_manager.start()          # called once at boot
    browser_manager.stop()           # called on graceful shutdown

Usage (via card_renderer.py):
    png = browser_manager.render_with_page(lambda page: render_fn(page))
"""

from __future__ import annotations

import logging
import queue
import threading
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
    the higher-level ``render_with_page()``.  This prevents the
    asyncio-loop / eventlet-hub conflict that occurs when Playwright
    objects are accessed from green threads.
    """

    def __init__(self, pool_size: int = POOL_SIZE) -> None:
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._started = False

        # Dedicated OS thread that owns all Playwright objects
        self._worker: Optional[threading.Thread] = None
        self._job_q: queue.Queue = queue.Queue()

        # Page pool — queue is thread-safe; Playwright objects inside are
        # only *touched* from within the worker thread via call_in_thread.
        self._page_pool: queue.Queue = queue.Queue(maxsize=pool_size)

        # Thread-safe health flag — set/cleared only by the worker thread
        # via _set_healthy().  Used by is_running so we never call
        # browser.is_connected() off the worker thread.
        self._healthy: bool = False

        # Playwright handles — only accessed from _worker thread
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Launch Chromium inside the dedicated Playwright thread and fill the pool.

        Blocks until Chromium is ready.  Safe to call multiple times.
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

            # Bootstrap: submit _launch() to the new thread and wait for it.
            # We cannot use call_in_thread() yet (_started is False), so we
            # build the job manually.
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
            ready = done.wait(timeout=120)  # Chromium launch can take ~30 s in containers
            if not ready:
                raise TimeoutError("BrowserManager: Chromium did not launch within 120 s.")

            if exc_holder[0] is not None:
                raise exc_holder[0]

            self._started = True
            # _browser.version accessed here in the caller thread is safe because
            # _launch_job has fully completed (done event was set) and the worker
            # thread has moved on; version is a read of a simple string attribute.
            logger.info(
                "BrowserManager started — %d pages ready (Chromium %s).",
                self._pool_size,
                self._browser.version if self._browser else "unknown",
            )

    def stop(self) -> None:
        """Tear down the browser and stop the worker thread.  Safe to call even if never started."""
        with self._lock:
            if not self._started:
                return

            # Submit teardown to the worker thread
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

            # Poison pill — stops _playwright_thread_main loop
            self._job_q.put(None)
            if self._worker:
                self._worker.join(timeout=15)

            self._started = False
            self._healthy = False
            logger.info("BrowserManager stopped.")

    @property
    def is_running(self) -> bool:
        # Uses _healthy (set/cleared by the worker thread) instead of
        # calling browser.is_connected() here — that would access a Playwright
        # object from the wrong thread and re-introduce the asyncio conflict.
        return self._started and self._healthy

    def _set_healthy(self, value: bool) -> None:
        """Called from the worker thread to update the health flag safely."""
        self._healthy = value

    # ── Call dispatch ─────────────────────────────────────────────────────────

    def call_in_thread(self, fn: Callable[[], T], timeout: Optional[float] = None) -> T:
        """
        Run *fn()* inside the dedicated Playwright OS thread and return its result.

        Raises whatever *fn* raised.  ``threading.Event.wait()`` is
        eventlet-patched to green-yield, so callers in green threads do not block
        the eventlet hub.

        Reentrancy: if called from the worker thread itself (e.g. from within a
        job submitted by render_with_page), *fn* is executed directly rather than
        re-queued — which would deadlock.
        """
        if not self._started:
            raise RuntimeError("BrowserManager is not running. Call start() first.")

        # Reentrancy guard: if the caller IS the playwright worker thread,
        # run directly to avoid self-deadlock (submit → wait → never dequeued).
        if threading.current_thread() is self._worker:
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
            raise TimeoutError(
                f"BrowserManager.call_in_thread: job did not complete within {timeout}s."
            )

        if exc_holder[0] is not None:
            raise exc_holder[0]
        return result_holder[0]  # type: ignore[return-value]

    # ── Page borrowing ────────────────────────────────────────────────────────

    def render_with_page(self, fn: Callable[[Page], T], timeout: float = 60.0) -> T:
        """
        Borrow a page from the pool, call *fn(page)*, and return the result.

        The acquire → use → health-check → reset → return cycle all runs inside
        the Playwright thread via ``call_in_thread()``.  *fn* receives the Page
        object but must not store it or call it from another thread.

        Raises ``queue.Empty`` if no page is available within *timeout* seconds.
        Raises ``RuntimeError`` if the BrowserManager is not running.
        """
        if not self.is_running:
            raise RuntimeError(
                "BrowserManager is not running. Call start() before render_with_page()."
            )

        def _borrow_and_use() -> T:
            try:
                page: Page = self._page_pool.get(timeout=timeout)
            except queue.Empty as exc:
                raise queue.Empty(
                    f"No Playwright page available within {timeout}s "
                    f"(pool_size={self._pool_size})"
                ) from exc

            crashed = False
            try:
                result = fn(page)
            except Exception:
                crashed = True
                raise
            finally:
                if crashed or not self._is_page_healthy(page):
                    logger.warning("Page crashed or unhealthy — replacing with a fresh one.")
                    try:
                        page.close()
                    except Exception:
                        pass
                    try:
                        replacement = self._new_page()
                        self._page_pool.put_nowait(replacement)
                    except Exception as exc:
                        logger.error(
                            "Could not create replacement page (%s) — attempting browser restart.", exc
                        )
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
        """
        Dedicated OS thread main loop.

        Processes jobs from ``_job_q`` one at a time.  A ``None`` sentinel
        signals shutdown.  All Playwright objects are created and touched
        exclusively here, ensuring they share the same asyncio event-loop context.
        """
        while True:
            job = self._job_q.get()
            if job is None:
                break  # shutdown sentinel
            try:
                job()
            except Exception as exc:
                # The job itself is responsible for capturing its exception via
                # exc_holder + done.set().  This catch is a safety net only.
                logger.error("Unhandled error in playwright worker job: %s", exc)

    # ── Playwright internals (run only from _worker thread) ───────────────────

    def _launch(self) -> None:
        """Start Playwright + Chromium and warm the pool.  Called in worker thread."""
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=_CHROMIUM_ARGS, headless=True)

        for _ in range(self._pool_size):
            page = self._new_page()
            self._page_pool.put_nowait(page)

        self._set_healthy(True)

    def _teardown(self) -> None:
        """Drain pool, close pages, close browser.  Called in worker thread."""
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
        """Create a fresh card-sized page in the browser context.  Worker thread only."""
        return self._browser.new_page(
            viewport={"width": CARD_W, "height": CARD_H},
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )

    def _attempt_pool_recovery(self) -> None:
        """
        Emergency pool recovery: restart the browser and refill the pool.
        Runs synchronously inside the worker thread.
        """
        logger.warning("BrowserManager: starting emergency pool recovery...")
        try:
            # Drain remaining pages
            while not self._page_pool.empty():
                try:
                    p = self._page_pool.get_nowait()
                    p.close()
                except Exception:
                    pass
            # Tear down old browser
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
            # Re-launch
            self._launch()
            logger.info(
                "BrowserManager: pool recovery successful (%d pages ready).", self._pool_size
            )
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
        """Navigate to about:blank so the next render starts clean."""
        try:
            page.goto("about:blank", wait_until="commit")
        except Exception:
            pass


# Module-level singleton — lifecycle.py calls start()/stop() on this.
browser_manager = BrowserManager(pool_size=POOL_SIZE)
