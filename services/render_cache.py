"""
Kanyoza Systems AI Platform v10 — RenderCache (M5).

Caches rendered card PNGs to avoid re-running the full
SVG → HTML → Playwright pipeline for identical content.

Two-tier storage:
  L1 — in-memory LRU dict (fast, bounded by MAX_ENTRIES)
  L2 — disk under /tmp/kanyoza_cards/ (survives in-process reloads,
       bounded by TTL and a periodic sweep)

Cache key = SHA-256 of (RENDER_VERSION + rendered HTML string).
Including the version salt ensures that any change to the rendering
pipeline (template, fonts, layout tokens, device scale) immediately
invalidates all cached PNGs without waiting for TTL expiry.

Bump RENDER_VERSION whenever a change to the visual output is deployed
(card.html, layout.py, fonts.py, browser args, device scale factor).

Thread-safety: all public methods are protected by a threading.RLock.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kanyoza.services.render_cache")

# Bump this whenever a visual change is deployed (card.html, layout.py,
# fonts.py, device scale, browser args, etc.) to invalidate all cached PNGs
# without waiting for TTL expiry.  Format: YYYYMMDD-NN (date + sequence).
RENDER_VERSION = "20260703-01"

# L1 memory bounds
MAX_ENTRIES = 50        # how many PNGs to keep in RAM

# L2 disk bounds
DISK_DIR = Path("/tmp/kanyoza_cards")
DISK_TTL_SECONDS = 86_400   # 24 hours
MAX_DISK_ENTRIES = 200


class RenderCache:
    """
    Two-tier PNG cache: in-memory LRU (L1) → disk (L2).

    Usage:
        key = render_cache.key_for(html)
        png = render_cache.get(key)
        if png is None:
            png = <do the actual render>
            render_cache.set(key, png)
    """

    def __init__(
        self,
        max_entries: int = MAX_ENTRIES,
        disk_ttl: int = DISK_TTL_SECONDS,
        disk_dir: Path = DISK_DIR,
    ) -> None:
        self._lock = threading.RLock()
        self._max = max_entries
        self._disk_ttl = disk_ttl
        self._disk_dir = disk_dir
        # OrderedDict used as an LRU: most-recently-used at the right end
        self._lru: OrderedDict[str, bytes] = OrderedDict()
        self._hits_l1 = 0
        self._hits_l2 = 0
        self._misses = 0
        self._writes = 0   # dedicated counter — sweep cadence is based on writes, not reads
        self._ensure_disk_dir()

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def key_for(html: str) -> str:
        """
        Return the cache key for a given HTML string.

        Key = SHA-256 of (RENDER_VERSION + html) so that bumping
        RENDER_VERSION at the top of this file immediately invalidates
        all cached PNGs without relying on TTL expiry.
        """
        payload = (RENDER_VERSION + html).encode("utf-8", errors="replace")
        return hashlib.sha256(payload).hexdigest()

    def get(self, key: str) -> Optional[bytes]:
        """Return cached PNG bytes, or None on a cache miss."""
        with self._lock:
            # L1 hit
            if key in self._lru:
                self._lru.move_to_end(key)
                self._hits_l1 += 1
                logger.debug("RenderCache L1 hit  key=%.12s…", key)
                return self._lru[key]

            # L2 hit
            png = self._disk_get(key)
            if png is not None:
                self._promote(key, png)
                self._hits_l2 += 1
                logger.debug("RenderCache L2 hit  key=%.12s…", key)
                return png

            self._misses += 1
            logger.debug("RenderCache miss     key=%.12s…", key)
            return None

    def set(self, key: str, png: bytes) -> None:
        """Store a rendered PNG in both tiers."""
        if not png:
            return
        with self._lock:
            self._writes += 1
            self._promote(key, png)
            self._disk_set(key, png)

    def clear(self) -> None:
        """Flush both tiers (useful in tests or after a major template change)."""
        with self._lock:
            self._lru.clear()
            self._sweep_disk(max_age=0)   # delete everything
            logger.info("RenderCache cleared.")

    def stats(self) -> dict:
        with self._lock:
            total = self._hits_l1 + self._hits_l2 + self._misses
            hit_rate = (self._hits_l1 + self._hits_l2) / total if total else 0.0
            return {
                "l1_entries": len(self._lru),
                "hits_l1": self._hits_l1,
                "hits_l2": self._hits_l2,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 3),
            }

    # ── L1 helpers ─────────────────────────────────────────────────────────────

    def _promote(self, key: str, png: bytes) -> None:
        """Insert/refresh key in the in-memory LRU, evicting the oldest if full."""
        if key in self._lru:
            self._lru.move_to_end(key)
        else:
            if len(self._lru) >= self._max:
                oldest_key, _ = self._lru.popitem(last=False)
                logger.debug("RenderCache L1 evict key=%.12s…", oldest_key)
            self._lru[key] = png

    # ── L2 helpers ─────────────────────────────────────────────────────────────

    def _disk_path(self, key: str) -> Path:
        return self._disk_dir / f"{key}.png"

    def _disk_get(self, key: str) -> Optional[bytes]:
        path = self._disk_path(key)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self._disk_ttl:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            logger.warning("RenderCache disk read error: %s", exc)
            return None

    def _disk_set(self, key: str, png: bytes) -> None:
        try:
            self._disk_path(key).write_bytes(png)
            self._maybe_sweep_disk()
        except OSError as exc:
            logger.warning("RenderCache disk write error: %s", exc)

    def _ensure_disk_dir(self) -> None:
        try:
            self._disk_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("RenderCache: cannot create disk dir %s: %s", self._disk_dir, exc)

    def _maybe_sweep_disk(self) -> None:
        """Sweep disk every 100 writes (write counter — not polluted by reads)."""
        if self._writes % 100 == 0:
            self._sweep_disk()

    def _sweep_disk(self, max_age: Optional[int] = None) -> None:
        """Remove expired or excess files from the disk cache."""
        if max_age is None:
            max_age = self._disk_ttl
        try:
            now = time.time()
            entries = sorted(
                self._disk_dir.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
            )
            for path in entries:
                age = now - path.stat().st_mtime
                if age > max_age:
                    path.unlink(missing_ok=True)

            # Enforce MAX_DISK_ENTRIES — delete oldest first
            remaining = sorted(
                self._disk_dir.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
            )
            for path in remaining[:-MAX_DISK_ENTRIES] if len(remaining) > MAX_DISK_ENTRIES else []:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("RenderCache disk sweep error: %s", exc)


# Module-level singleton
render_cache = RenderCache()
