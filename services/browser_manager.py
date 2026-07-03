"""
Kanyoza Systems AI Platform v10 — BrowserManager (M5).

Owns a single long-lived Playwright Chromium process and a pool of N
reusable pages.  Launching Chromium costs ~500ms; reusing pages drops
individual card renders to ~100ms.

Usage (via lifecycle.py):
    browser_manager.start()          # called once at boot
    browser_manager.stop()           # called on graceful shutdown

Usage (via card_renderer.py):
    with browser_manager.acquire_page() as page:
        page.set_content(html, wait_until="networkidle")
        png = page.screenshot(...)

Thread-safety:
    acquire_page() blocks until a page is available (queue.Queue.get).
    The pool refills automatically if a page crashes.
"""

from __future__ import annotations

import logging
import queue
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page, Playwright

logger = logging.getLogger("kanyoza.services.browser_manager")

# How many Playwright pages to keep alive simultaneously.
# 3 is enough for the current task-queue worker count (3 workers).
POOL_SIZE = 1
CARD_W = 1080
CARD_H = 1080
DEVICE_SCALE_FACTOR = 2

# Chromium flags that silence sandbox/shm warnings in a container env
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

    Call start() during application boot and stop() on shutdown.
    Use acquire_page() as a context manager to borrow a page from the pool.
    """

    def __init__(self, pool_size: int = POOL_SIZE) -> None:
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._started = False

        # Playwright / browser handles — populated by start()
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

        # Queue of ready-to-use pages.  maxsize == pool_size keeps it bounded.
        self._pool: queue.Queue[Page] = queue.Queue(maxsize=pool_size)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Launch Chromium and fill the page pool.

        Safe to call multiple times — subsequent calls are no-ops.
        Raises on import error (Playwright not installed) or launch failure.
        """
        with self._lock:
            if self._started:
                return
            self._launch()
            self._started = True
            logger.info(
                "BrowserManager started — %d pages ready (Chromium %s).",
                self._pool_size,
                self._browser.version if self._browser else "unknown",
            )

    def stop(self) -> None:
        """
        Close all pooled pages and shut down the browser process.

        Safe to call even if start() was never called.
        """
        with self._lock:
            if not self._started:
                return
            self._teardown()
            self._started = False
            logger.info("BrowserManager stopped.")

    @property
    def is_running(self) -> bool:
        return self._started and self._browser is not None and self._browser.is_connected()

    # ── Page acquisition ───────────────────────────────────────────────────────

    @contextmanager
    def acquire_page(self, timeout: float = 30.0) -> Generator[Page, None, None]:
        """
        Borrow a page from the pool.

        Blocks up to *timeout* seconds waiting for a free page.
        After the with-block, the page is reset and returned to the pool.
        If the page crashed during use, it is replaced with a fresh one.

        Raises:
            RuntimeError — if the browser is not running.
            queue.Empty — if no page becomes available within *timeout*.
        """
        if not self.is_running:
            raise RuntimeError(
                "BrowserManager is not running. Call start() before acquire_page()."
            )

        try:
            page: Page = self._pool.get(timeout=timeout)
        except queue.Empty as exc:
            raise queue.Empty(
                f"No Playwright page available within {timeout}s "
                f"(pool_size={self._pool_size})"
            ) from exc

        crashed = False
        try:
            yield page
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
                    self._pool.put_nowait(replacement)
                except Exception as exc:
                    logger.error(
                        "Could not create replacement page (%s) — attempting browser restart.", exc
                    )
                    self._attempt_pool_recovery()
            else:
                self._reset_page(page)
                try:
                    self._pool.put_nowait(page)
                except queue.Full:
                    # Pool somehow overflowed — just close the extra page
                    try:
                        page.close()
                    except Exception:
                        pass

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _launch(self) -> None:
        """Start Playwright + Chromium and warm the pool.  Called under _lock."""
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=_CHROMIUM_ARGS, headless=True)

        for _ in range(self._pool_size):
            page = self._new_page()
            self._pool.put_nowait(page)

    def _teardown(self) -> None:
        """Drain the pool, close pages, close browser.  Called under _lock."""
        while not self._pool.empty():
            try:
                page = self._pool.get_nowait()
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
        """Create a fresh card-sized page in the browser context."""
        page = self._browser.new_page(
            viewport={"width": CARD_W, "height": CARD_H},
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )
        return page

    def _attempt_pool_recovery(self) -> None:
        """
        Emergency pool recovery: restart the browser process and refill the pool.

        Called when a replacement page cannot be created (e.g. browser crashed).
        Runs synchronously in the caller's thread — the pool will be temporarily
        empty while recovery is in progress, causing other callers to block on
        queue.get until it completes or times out.
        """
        logger.warning("BrowserManager: starting emergency pool recovery...")
        try:
            with self._lock:
                # Drain any remaining pages from the pool
                while not self._pool.empty():
                    try:
                        p = self._pool.get_nowait()
                        p.close()
                    except Exception:
                        pass
                # Tear down the old browser process
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
                # Re-launch the browser and fill the pool
                self._launch()
            logger.info("BrowserManager: pool recovery successful (%d pages ready).", self._pool_size)
        except Exception as exc:
            logger.error("BrowserManager: pool recovery failed: %s", exc)

    @staticmethod
    def _is_page_healthy(page: Page) -> bool:
        try:
            # Evaluate a trivial expression; raises if the page process is dead
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


# Module-level singleton — the lifecycle module calls start()/stop() on this.
browser_manager = BrowserManager(pool_size=POOL_SIZE)
